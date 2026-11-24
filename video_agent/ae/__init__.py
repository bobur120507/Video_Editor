"""Matching After Effects' results without AE: clean-room re-implementations from public sources.

curves     AE expression functions (linear/ease, wiggle, loopOut, smooth) and Lottie/CSS bezier easing
lottie     Lottie/Bodymovin JSON (an AE comp exported with Bodymovin) -> Blueprint
mogrt      Motion Graphics Template (.mogrt) controls from its definition.json
effects    Paper techniques behind popular plugins: bloom (Deep Glow), motion-vector blur (RSMB),
           optical-flow retiming (Twixtor), parametric shake (S_Shake)

Every function names the public document or paper it follows (docs/research.md); no Adobe or plugin
code or presets are used. See video_agent/knowledge/techniques.yaml for the parameters and their sources.
"""
