#!/usr/bin/env bash
# One command to put Secret Santa in your own AWS account: API + table first, then the static site.
#   ./deploy.sh                      uses your default AWS profile and region
#   ./deploy.sh --region us-east-2 --profile santa
# Use the Region your account can deploy to. Accounts from the 2026 "new sign-up" are pinned to one Region
# (settings.aws.com > Project > Additional Info); any other Region fails with "explicit deny in a service control policy".
# Run it again any time to push changes. Remove everything with:  sam delete --stack-name secret-santa
set -euo pipefail
cd "$(dirname "$0")"

STACK="${STACK:-secret-santa}"
for tool in sam aws zip curl perl; do
  command -v "$tool" >/dev/null || { echo "Needs '$tool' on your PATH (sam: brew install aws-sam-cli, aws: brew install awscli)"; exit 1; }
done

echo "== 1/3  API, table and the Amplify app"
sam deploy \
  --template-file template.yaml \
  --stack-name "$STACK" \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  "$@"

API_URL="$(aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" --output text "$@")"
SITE_URL="$(aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='SiteUrl'].OutputValue" --output text "$@")"
APP_ID="$(aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='AmplifyAppId'].OutputValue" --output text "$@")"
[ -n "$API_URL" ] && [ -n "$SITE_URL" ] && [ -n "$APP_ID" ] || { echo "Could not read the stack outputs."; exit 1; }

echo "== 2/3  Packing the site (it is told where the API lives)"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
mkdir -p "$BUILD/site"
cp -R web/. "$BUILD/site/"
printf 'window.SANTA_API = "%s";\n' "${API_URL%/}" > "$BUILD/site/config.js"
SITE_ORIGIN="${SITE_URL%/}" perl -pi -e 's/\@\@ORIGIN\@\@/$ENV{SITE_ORIGIN}/g' "$BUILD/site/index.html"
(cd "$BUILD/site" && zip -qr ../site.zip .)

echo "== 3/3  Uploading the site to Amplify"
read -r JOB_ID UPLOAD_URL <<< "$(aws amplify create-deployment --app-id "$APP_ID" --branch-name main --query '[jobId,zipUploadUrl]' --output text "$@")"
curl -sS --fail -H "Content-Type: application/zip" --upload-file "$BUILD/site.zip" "$UPLOAD_URL" >/dev/null
aws amplify start-deployment --app-id "$APP_ID" --branch-name main --job-id "$JOB_ID" "$@" >/dev/null
STATUS="PENDING"
for _ in $(seq 1 60); do
  STATUS="$(aws amplify get-job --app-id "$APP_ID" --branch-name main --job-id "$JOB_ID" --query 'job.summary.status' --output text "$@")"
  case "$STATUS" in SUCCEED) break ;; FAILED|CANCELLED) echo "Amplify deployment $STATUS. See the Amplify console for app $APP_ID."; exit 1 ;; esac
  sleep 3
done
[ "$STATUS" = "SUCCEED" ] || echo "Amplify is still deploying (status: $STATUS). Give it a minute."

echo
echo "Website:  $SITE_URL"
echo "API:      $API_URL"
echo
echo "Open the website, create your event, and keep the organizer link it gives you."
