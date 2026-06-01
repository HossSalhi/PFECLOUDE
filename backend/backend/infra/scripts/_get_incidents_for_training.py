import urllib.request, json, base64, re

creds = base64.b64encode(b"operator:operator-change-me").decode()

def api_get(path):
    req = urllib.request.Request(f"http://192.168.148.153:30808{path}")
    req.add_header("Authorization", f"Basic {creds}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)

type_counts = {}
action_counts = {}
exec_mode_counts = {}
severity_counts = {}
total = 0
page = 0
while True:
    data = api_get(f"/api/incidents?size=100&page={page}&sort=id,asc")
    items = data.get("content", [])
    if not items:
        break
    for inc in items:
        if inc.get("source") == "AI":
            total += 1
            desc = inc.get("description", "")
            sev = inc.get("severity", "UNKNOWN")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
            # Extract type from description: "type=XXXXX"
            m = re.search(r'type=([A-Z_]+)', desc)
            t = m.group(1) if m else "UNKNOWN"
            type_counts[t] = type_counts.get(t, 0) + 1
            # Extract action
            m2 = re.search(r'action=([A-Z_]+)', desc)
            if m2:
                a = m2.group(1)
                action_counts[a] = action_counts.get(a, 0) + 1
            # Extract executionMode
            m3 = re.search(r'executionMode=([A-Z_]+)', desc)
            if m3:
                e = m3.group(1)
                exec_mode_counts[e] = exec_mode_counts.get(e, 0) + 1
    total_pages = data.get("page", {}).get("totalPages", 1)
    page += 1
    if page >= total_pages:
        break

print(f"Total AI incidents: {total}")
print("\nBy incident type:")
for k, v in sorted(type_counts.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")
print("\nBy severity:")
for k, v in sorted(severity_counts.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")
print("\nBy execution mode:")
for k, v in sorted(exec_mode_counts.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")
print("\nTop 10 actions:")
for k, v in sorted(action_counts.items(), key=lambda x: -x[1])[:10]:
    print(f"  {k}: {v}")
