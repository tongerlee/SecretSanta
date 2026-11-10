#!/usr/bin/env bash
# Mint a fresh organizer link for an event, straight in the table. For the AWS account owner only: the site itself
# cannot give a lost organizer link back (it stores only a hash of the key). The old link stops working at once.
#
#   ./recover.sh <event-id> [--profile santa --region us-east-2]
#
# The event id is the 12 characters after ?e= in any link to the event.
set -euo pipefail
cd "$(dirname "$0")"
STACK="${STACK_NAME:-secret-santa}"

ID="${1:-}"; shift || true
[[ "$ID" =~ ^[a-z0-9]{12}$ ]] || { echo "usage: $0 <event-id> [aws cli options]   e.g. $0 kr4trywka3y4 --profile santa --region us-east-2"; exit 1; }

TABLE="$(aws cloudformation describe-stack-resource --stack-name "$STACK" --logical-resource-id Table --query 'StackResourceDetail.PhysicalResourceId' --output text "$@")"
SITE="$(aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='SiteUrl'].OutputValue" --output text "$@")"
[ -n "$TABLE" ] && [ "$TABLE" != "None" ] || { echo "Could not find the table in stack '$STACK'. Has deploy.sh run with these options?"; exit 1; }

TITLE="$(aws dynamodb get-item --table-name "$TABLE" --key "{\"pk\":{\"S\":\"E#$ID\"}}" --projection-expression title --query 'Item.title.S' --output text "$@")"
[ -n "$TITLE" ] && [ "$TITLE" != "None" ] || { echo "No event with id $ID (it may have been deleted)."; exit 1; }

KEY="$(openssl rand -hex 16)"
HASH="$(printf '%s' "$KEY" | openssl dgst -sha256 -binary | base64)"
# Bumping v makes any request that read the old version retry against the new one (see mutate in api/index.mjs).
aws dynamodb update-item --table-name "$TABLE" --key "{\"pk\":{\"S\":\"E#$ID\"}}" \
  --update-expression 'SET adminHash = :h ADD #v :one' \
  --condition-expression 'attribute_exists(pk)' \
  --expression-attribute-names '{"#v":"v"}' \
  --expression-attribute-values "{\":h\":{\"S\":\"$HASH\"},\":one\":{\"N\":\"1\"}}" "$@" >/dev/null

echo "New organizer link for \"$TITLE\" (the old one no longer works):"
echo
echo "  ${SITE%/}/?e=$ID#admin=$KEY"
echo
echo "Open it on your device once; the browser keeps the key from then on. Keep it to yourself."
