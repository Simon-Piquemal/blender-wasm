"""Staging-time OCIO diet (see link_blender_web.sh).

A browser canvas is an sRGB display: drop the Display P3 / Rec.1886 /
Rec.2020 / Rec.2100 display definitions and the multi-MB AgX display cubes
they reference, plus the niche Khronos PBR Neutral view (5.3MB cube).
AgX_Base_Rec2020.cube STAYS: the kept False Color view transform samples it.
Only the STAGED copy is patched; the source tree is untouched.
"""
import os
import re
import sys

d = sys.argv[1]
# Set to False to keep the AgX/Filmic views and their ~7.8 MB of LUTs.
DROP_AGX_FILMIC = True
cfg = d + "/config.ocio"
lines = open(cfg).read().splitlines(True)
out = []
i = 0
while i < len(lines):
    l = lines[i]
    if l.startswith("displays:"):
        out.append(l)
        i += 1
        keep = False
        while i < len(lines) and (lines[i].startswith("  ") or lines[i].strip() == ""):
            if re.match(r"^  \S", lines[i]):
                keep = lines[i].startswith("  sRGB:")
            if keep and "Khronos PBR Neutral" not in lines[i]:
                out.append(lines[i])
            i += 1
        continue
    if l.startswith("active_displays:"):
        out.append("active_displays: [sRGB]\n")
        i += 1
        continue
    if l.startswith("active_views:"):
        out.append("active_views: [Standard, ACES 1.3, ACES 2.0, AgX, Filmic, "
                   "Filmic Log, False Color, Raw]\n")
        i += 1
        continue
    out.append(l)
    i += 1
open(cfg, "w").write("".join(out))
# Idempotent on purpose: the config rewrite above is a no-op on an already
# trimmed tree, so the lut removal must not explode either. The count is
# printed so a silent upstream rename shows up as "0 of 3" instead of nothing.
removed = 0
luts = ("pbrNeutral.cube", "AgX_Base_P3.cube", "AgX_Rec2100-HLG_p3_lim.cube")
for f in luts:
    path = d + "/luts/" + f
    if os.path.exists(path):
        os.remove(path)
        removed += 1
print("OCIO trimmed ({} of {} display luts removed)".format(removed, len(luts)))

# --- drop the AgX and Filmic view transforms -------------------------------
# ~7.8 MB of LUTs for four views this build never offers. The colorspace and
# view_transform DEFINITIONS stay on purpose: OCIO resolves a FileTransform
# lazily, when a processor is built, so a definition nobody selects never
# touches its (now absent) file. Removing the *views* is what makes them
# unselectable. Blender's startup file asks for AgX, so webapp_ui.py forces the
# scene back to Standard -- without that the scene would name a view that no
# longer exists.
if DROP_AGX_FILMIC:
    drop_views = {"AgX", "Filmic", "Filmic Log", "False Color"}
    kept = []
    view_re = re.compile(r"^    - !<View> \{name: ([^,]+),")
    for line in open(cfg).read().splitlines(True):
        m = view_re.match(line)
        if m and m.group(1) in drop_views:
            continue
        if line.startswith("active_views:"):
            line = "active_views: [Standard, ACES 1.3, ACES 2.0, Raw]" + chr(10)
        kept.append(line)
    open(cfg, "w").write("".join(kept))
    gone = 0
    for f in ("luts/AgX_Base_Rec2020.cube", "luts/AgX_Base_sRGB.cube",
              "luts/AgX_False_Color.spi1d",
              "luts/luminance_compensation_bt2020.cube",
              "filmic/filmic_desat_33.cube"):
        path = d + "/" + f
        if os.path.exists(path):
            os.remove(path)
            gone += 1
    fdir = d + "/filmic"
    if os.path.isdir(fdir):
        for f in os.listdir(fdir):
            if f.startswith("filmic_to_"):
                os.remove(fdir + "/" + f)
                gone += 1
    print("OCIO: AgX + Filmic views dropped ({} lut files removed)".format(gone))

