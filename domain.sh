#!/usr/bin/env bash
# Attach the custom domain (the CustomDomain default in template.yaml) to the Amplify site, then print the two
# DNS records to add at the domain's DNS provider, in the Name / Points-to form Shopify's "Add custom record" uses.
# Safe to run again and again: the first run creates the domain in Amplify, later runs just show records and status.
#
#   ./domain.sh --profile santa --region us-east-2      (same extra arguments as deploy.sh)
#
set -euo pipefail
cd "$(dirname "$0")"
for tool in aws python3; do command -v "$tool" >/dev/null || { echo "Please install $tool first."; exit 1; }; done

STACK="${STACK_NAME:-secret-santa}"
FULL="$(python3 -c "
import re,sys
t=open('template.yaml').read()
m=re.search(r'CustomDomain:\s*\n(?:.*\n)*?\s*Default:\s*(\S+)',t)
print(m.group(1) if m else '')")"
[ -n "$FULL" ] || { echo "No CustomDomain default in template.yaml."; exit 1; }
PREFIX="${FULL%%.*}"          # gifts
ROOT="${FULL#*.}"             # departmentofunnecessarythings.com

APP_ID="$(aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='AmplifyAppId'].OutputValue" --output text "$@")"
[ -n "$APP_ID" ] && [ "$APP_ID" != "None" ] || { echo "Could not read the Amplify app id from the stack '$STACK'. Has deploy.sh run?"; exit 1; }

if ! aws amplify get-domain-association --app-id "$APP_ID" --domain-name "$ROOT" "$@" >/dev/null 2>&1; then
  echo "Creating $FULL in Amplify (subdomain only; the root domain and www stay where they are)..."
  aws amplify create-domain-association --app-id "$APP_ID" --domain-name "$ROOT" \
    --sub-domain-settings "prefix=$PREFIX,branchName=main" --no-enable-auto-sub-domain "$@" >/dev/null
fi

# Amplify needs a moment before it knows the certificate's verification record.
for _ in $(seq 1 20); do
  JSON="$(aws amplify get-domain-association --app-id "$APP_ID" --domain-name "$ROOT" --output json "$@")"
  if python3 -c "import json,sys; d=json.loads(sys.argv[1])['domainAssociation']; sys.exit(0 if d.get('certificateVerificationDNSRecord') and all(s.get('dnsRecord') for s in d['subDomains']) else 1)" "$JSON"; then break; fi
  sleep 3
done

python3 - "$JSON" "$ROOT" <<'EOF'
import json, sys
d = json.loads(sys.argv[1])["domainAssociation"]; root = sys.argv[2]
status = d.get("domainStatus", "?")
def split(rec):
    # Amplify gives "name CNAME target" with an optional trailing dot; Shopify wants the name without the domain part.
    name, _, target = rec.split()
    name = name.rstrip(".")
    if name.endswith("." + root): name = name[: -len(root) - 1]
    return name, target.rstrip(".")
rows = []
cert = d.get("certificateVerificationDNSRecord") or (d.get("certificate") or {}).get("certificateVerificationDNSRecord")
if cert: rows.append(("1. certificate check (add this one first)",) + split(cert))
for s in d.get("subDomains", []):
    if s.get("dnsRecord"): rows.append((f"2. the site itself ({s['subDomainSetting']['prefix']}.{root})",) + split(s["dnsRecord"]))
print()
print(f"Domain status in Amplify: {status}")
print()
if status == "AVAILABLE":
    print(f"Done: https://{d['subDomains'][0]['subDomainSetting']['prefix']}.{root}/ is live. Run ./deploy.sh if you have not since adding the domain.")
else:
    print("Add these in Shopify: Settings > Domains > the domain > Domain settings > Edit DNS settings > Add custom record > CNAME record")
    for label, name, target in rows:
        print(f"\n  {label}")
        print(f"    Type       CNAME")
        print(f"    Name       {name}")
        print(f"    Points to  {target}")
    print()
    print("Leave the existing A record and the www CNAME alone. Then run this script again to watch the status;")
    print("PENDING_VERIFICATION -> PENDING_DEPLOYMENT -> AVAILABLE usually takes minutes, sometimes hours.")
    if d.get("statusReason"): print(f"\nAmplify says: {d['statusReason']}")
EOF
