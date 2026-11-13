"""Database: brand identity, blueprints, template vault, published fingerprints, learning weights."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .blueprint import Blueprint
from .config import get_settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class BrandIdentity(Base):
    __tablename__ = "brand_identity"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    colors: Mapped[list] = mapped_column(JSON, default=list)
    font_path: Mapped[str | None] = mapped_column(String(512))
    logo_path: Mapped[str | None] = mapped_column(String(512))
    tone_samples: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class EditBlueprint(Base):
    __tablename__ = "edit_blueprints"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    data: Mapped[dict] = mapped_column(JSON)
    source_sha256: Mapped[str] = mapped_column(String(64), index=True, default="")
    origin: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    @property
    def blueprint(self) -> Blueprint:
        return Blueprint.model_validate(self.data)


class TemplateVault(Base):
    __tablename__ = "template_vault"
    __table_args__ = (UniqueConstraint("user_id", "nickname"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    nickname: Mapped[str] = mapped_column(String(64))
    blueprint_id: Mapped[int] = mapped_column(ForeignKey("edit_blueprints.id"))
    tags: Mapped[list] = mapped_column(JSON, default=list)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_views: Mapped[float | None] = mapped_column(Float)  # filled by /audit once Meta is connected
    preview_path: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PublishedMediaFingerprint(Base):
    __tablename__ = "published_media_fingerprints"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    blueprint_id: Mapped[int] = mapped_column(ForeignKey("edit_blueprints.id"))
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    tg_file_unique_id: Mapped[str | None] = mapped_column(String(128), index=True)
    tg_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger)
    ig_media_id: Mapped[str | None] = mapped_column(String(64))
    ig_shortcode: Mapped[str | None] = mapped_column(String(64), index=True)
    master_path: Mapped[str] = mapped_column(String(512), default="")
    caption: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LearningWeight(Base):
    __tablename__ = "learning_weights"
    __table_args__ = (UniqueConstraint("user_id", "key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


async def init_db() -> async_sessionmaker[AsyncSession]:
    global _engine, _sessionmaker
    if _sessionmaker is None:
        get_settings().dir()  # make sure the data dir exists for SQLite
        _engine = create_async_engine(get_settings().db_url)
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _sessionmaker


def session() -> AsyncSession:
    if _sessionmaker is None:
        raise RuntimeError("init_db() has not been called")
    return _sessionmaker()


# ---------------------------------------------------------------- repository helpers


async def save_blueprint(user_id: int, bp: Blueprint) -> EditBlueprint:
    async with session() as s:
        row = EditBlueprint(user_id=user_id, data=bp.model_dump(mode="json"), source_sha256=bp.source.sha256, origin=bp.source.origin)
        s.add(row)
        await s.commit()
        return row


async def get_blueprint(blueprint_id: int) -> EditBlueprint | None:
    async with session() as s:
        return await s.get(EditBlueprint, blueprint_id)


async def save_template(user_id: int, nickname: str, blueprint_id: int, tags: list[str] | None = None) -> TemplateVault:
    async with session() as s:
        existing = await s.scalar(select(TemplateVault).where(TemplateVault.user_id == user_id, TemplateVault.nickname == nickname))
        if existing:
            existing.blueprint_id = blueprint_id
            row = existing
        else:
            row = TemplateVault(user_id=user_id, nickname=nickname, blueprint_id=blueprint_id, tags=tags or [])
            s.add(row)
        await s.commit()
        return row


async def list_templates(user_id: int) -> list[TemplateVault]:
    async with session() as s:
        rows = await s.scalars(select(TemplateVault).where(TemplateVault.user_id == user_id).order_by(TemplateVault.usage_count.desc()))
        return list(rows)


async def get_template(user_id: int, template_id: int | None = None, nickname: str | None = None) -> TemplateVault | None:
    async with session() as s:
        q = select(TemplateVault).where(TemplateVault.user_id == user_id)
        q = q.where(TemplateVault.id == template_id) if template_id is not None else q.where(TemplateVault.nickname == nickname)
        return await s.scalar(q)


async def rename_template(user_id: int, template_id: int, nickname: str) -> bool:
    async with session() as s:
        row = await s.scalar(select(TemplateVault).where(TemplateVault.user_id == user_id, TemplateVault.id == template_id))
        if not row:
            return False
        row.nickname = nickname
        await s.commit()
        return True


async def delete_template(user_id: int, template_id: int) -> bool:
    async with session() as s:
        row = await s.scalar(select(TemplateVault).where(TemplateVault.user_id == user_id, TemplateVault.id == template_id))
        if not row:
            return False
        await s.delete(row)
        await s.commit()
        return True


async def bump_template_usage(template_id: int, preview_path: str | None = None) -> None:
    async with session() as s:
        row = await s.get(TemplateVault, template_id)
        if row:
            row.usage_count += 1
            if preview_path:
                row.preview_path = preview_path
            await s.commit()


async def add_fingerprint(**fields) -> PublishedMediaFingerprint:
    async with session() as s:
        row = PublishedMediaFingerprint(**fields)
        s.add(row)
        await s.commit()
        return row


async def find_fingerprint(
    user_id: int, *, tg_file_unique_id: str | None = None, file_sha256: str | None = None, ig_shortcode: str | None = None
) -> PublishedMediaFingerprint | None:
    async with session() as s:
        q = select(PublishedMediaFingerprint).where(PublishedMediaFingerprint.user_id == user_id)
        if tg_file_unique_id:
            q = q.where(PublishedMediaFingerprint.tg_file_unique_id == tg_file_unique_id)
        elif file_sha256:
            q = q.where(PublishedMediaFingerprint.file_sha256 == file_sha256)
        elif ig_shortcode:
            q = q.where(PublishedMediaFingerprint.ig_shortcode == ig_shortcode)
        else:
            return None
        return await s.scalar(q.order_by(PublishedMediaFingerprint.id.desc()))


async def update_fingerprint(fingerprint_id: int, **fields) -> None:
    async with session() as s:
        row = await s.get(PublishedMediaFingerprint, fingerprint_id)
        if row:
            for k, v in fields.items():
                setattr(row, k, v)
            await s.commit()


async def get_brand(user_id: int) -> BrandIdentity | None:
    async with session() as s:
        return await s.scalar(select(BrandIdentity).where(BrandIdentity.user_id == user_id))


async def upsert_brand(user_id: int, **fields) -> BrandIdentity:
    async with session() as s:
        row = await s.scalar(select(BrandIdentity).where(BrandIdentity.user_id == user_id))
        if row is None:
            row = BrandIdentity(user_id=user_id)
            s.add(row)
        for k, v in fields.items():
            setattr(row, k, v)
        await s.commit()
        return row


async def get_weights(user_id: int) -> dict[str, float]:
    async with session() as s:
        rows = await s.scalars(select(LearningWeight).where(LearningWeight.user_id == user_id))
        return {r.key: r.value for r in rows}


async def add_weights(user_id: int, deltas: dict[str, float]) -> None:
    async with session() as s:
        for key, delta in deltas.items():
            row = await s.scalar(select(LearningWeight).where(LearningWeight.user_id == user_id, LearningWeight.key == key))
            if row is None:
                row = LearningWeight(user_id=user_id, key=key, value=0.0)
                s.add(row)
            row.value = (row.value or 0.0) + delta
        await s.commit()
