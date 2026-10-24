import ast
import difflib
import sys

src, dst = sys.argv[1], sys.argv[2]
raw = open(src, "rb").read()
crlf = b"\r\n" in raw
s = raw.decode("utf-8").replace("\r\n", "\n")
old = "from hiddifypanel.antishare.scoring import age_trust, derive_state, pick_allowed_ips, record_seen, score_bump_for_excess\n"
assert old in s
s = s.replace(old, old + "from hiddifypanel.antishare.shared import load_networks, split_shared\n", 1)
old = "    current_ips = [ip for ip in recent_ips.get(user.uuid.lower(), []) if ip]\n"
assert old in s
s = s.replace(old, old + "    # CDN edges are shared by many users and are not their devices: they neither count towards the limit nor can they be banned (see antishare/shared.py)\n"
                         "    current_ips, shared_ips = split_shared(current_ips, load_networks())\n", 1)
old = '            "learning": learning,\n        },\n    )\n\n    if excess > 0'
assert old in s
s = s.replace(old, '            "learning": learning,\n            "shared_ips_ignored": len(shared_ips),\n        },\n    )\n\n    if excess > 0', 1)
ast.parse(s)
out = s.replace("\n", "\r\n") if crlf else s
open(dst, "wb").write(out.encode("utf-8"))
print("patched; original had CRLF:", crlf)
a = raw.decode("utf-8").replace("\r\n", "\n").splitlines()
b = s.splitlines()
with open(sys.argv[3], "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(difflib.unified_diff(a, b, "a/antishare/runner.py", "b/antishare/runner.py", lineterm="", n=2)) + "\n")
