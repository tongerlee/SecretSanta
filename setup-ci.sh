#!/usr/bin/env bash
# One-time: let GitHub Actions deploy this repository into your AWS account.
#
#   ./setup-ci.sh --profile santa --region us-east-2
#
# Preferred: GitHub signs each run with OIDC and assumes a role (no long-lived keys anywhere). Some accounts (the
# 2026 "new sign-up" ones) forbid adding an OIDC provider; then a deploy-only IAM user with an access key is made
# instead. Either way the permissions are the ones deploy.sh needs, scoped to this stack's names where the service
# allows it. The script finishes by writing the variables and secrets into the GitHub repository with `gh`, so the
# secret key never appears on screen (without gh it prints what to paste where). Safe to run again.
set -euo pipefail
cd "$(dirname "$0")"

REPO="${REPO:-tongerlee/SecretSanta}"
NAME="${NAME:-github-deploy-secret-santa}"      # the role, or the user
STACK="${STACK:-secret-santa}"
REGION="us-east-2"
for ((i = 1; i <= $#; i++)); do [ "${!i}" = "--region" ] && j=$((i + 1)) && REGION="${!j}"; done

ACCOUNT="$(aws sts get-caller-identity --query Account --output text "$@")"
PARTITION="$(aws sts get-caller-identity --query Arn --output text "$@" | cut -d: -f2)"
PROVIDER="arn:${PARTITION}:iam::${ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"

# Everything deploy.sh touches: the CloudFormation stack and SAM's upload bucket, the Lambda and its role, the table,
# the Amplify app, the alarms, plus reading the stack outputs.
POLICY="$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "Stack", "Effect": "Allow",
      "Action": ["cloudformation:*"],
      "Resource": ["arn:${PARTITION}:cloudformation:*:${ACCOUNT}:stack/${STACK}/*", "arn:${PARTITION}:cloudformation:*:${ACCOUNT}:stack/aws-sam-cli-managed-default/*"] },
    { "Sid": "StackReads", "Effect": "Allow",
      "Action": ["cloudformation:ListStacks", "cloudformation:ValidateTemplate", "cloudformation:GetTemplateSummary", "cloudformation:CreateChangeSet", "cloudformation:DescribeChangeSet"],
      "Resource": "*" },
    { "Sid": "Transform", "Effect": "Allow",
      "Action": ["cloudformation:CreateChangeSet"],
      "Resource": "arn:${PARTITION}:cloudformation:*:aws:transform/Serverless-2016-10-31" },
    { "Sid": "SamBucket", "Effect": "Allow",
      "Action": ["s3:*"],
      "Resource": ["arn:${PARTITION}:s3:::aws-sam-cli-managed-default-*", "arn:${PARTITION}:s3:::aws-sam-cli-managed-default-*/*"] },
    { "Sid": "SamBucketList", "Effect": "Allow",
      "Action": ["s3:ListAllMyBuckets", "s3:CreateBucket"],
      "Resource": "*" },
    { "Sid": "Resources", "Effect": "Allow",
      "Action": ["lambda:*", "dynamodb:*", "amplify:*", "cloudwatch:*", "sns:*", "logs:*"],
      "Resource": "*" },
    { "Sid": "LambdaRole", "Effect": "Allow",
      "Action": ["iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:UpdateRole", "iam:PassRole", "iam:TagRole", "iam:UntagRole",
                 "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:GetRolePolicy",
                 "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:UpdateAssumeRolePolicy"],
      "Resource": "arn:${PARTITION}:iam::${ACCOUNT}:role/${STACK}-*" }
  ]
}
JSON
)"

# ---------- hand the result to GitHub ----------
VARS=(); SECRETS=()      # name=value pairs
finish() {
  echo
  if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
    for kv in "${VARS[@]}"; do
      if [ -n "${kv#*=}" ]; then gh variable set "${kv%%=*}" --repo "$REPO" --body "${kv#*=}"
      else gh variable delete "${kv%%=*}" --repo "$REPO" >/dev/null 2>&1 || true; fi      # an empty value means "not this way"
    done
    for kv in "${SECRETS[@]}"; do printf '%s' "${kv#*=}" | gh secret set "${kv%%=*}" --repo "$REPO"; done
    echo "GitHub is set up. Every push to main now runs the tests and deploys (Actions tab shows each run)."
    echo "Optional: gh secret set ALERT_EMAIL --repo $REPO   (then type the email) for the API error alarm."
  else
    echo "gh is not signed in, so set these by hand at github.com/$REPO > Settings > Secrets and variables > Actions:"
    for kv in "${VARS[@]}"; do echo "  variable ${kv%%=*} = ${kv#*=}   $([ -z "${kv#*=}" ] && echo '(delete it if it exists)')"; done
    for kv in "${SECRETS[@]}"; do echo "  secret   ${kv%%=*} = ${kv#*=}"; done
    echo "(The secret key above is shown once; clear your terminal afterwards.)"
  fi
}

echo "== 1/2  How GitHub will sign in to account $ACCOUNT"
MODE=""
if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$PROVIDER" "$@" >/dev/null 2>&1; then
  MODE=oidc; echo "OIDC provider already there"
else
  OUT="$(aws iam create-open-id-connect-provider --url https://token.actions.githubusercontent.com \
          --client-id-list sts.amazonaws.com --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 "$@" 2>&1)" && MODE=oidc && echo "OIDC provider created"
  if [ "$MODE" != oidc ]; then
    if grep -q "service control policy\|AccessDenied" <<< "$OUT"; then
      echo "This account does not allow an OIDC provider (denied by a service control policy); using a deploy-only IAM user instead."
      MODE=keys
    else
      echo "$OUT"; exit 1
    fi
  fi
fi

echo "== 2/2  Permissions: what deploy.sh is allowed to do"
if [ "$MODE" = oidc ]; then
  TRUST="$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "$PROVIDER" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": "repo:${REPO}:ref:refs/heads/main"
      }
    }
  }]
}
JSON
)"
  if aws iam get-role --role-name "$NAME" "$@" >/dev/null 2>&1; then
    aws iam update-assume-role-policy --role-name "$NAME" --policy-document "$TRUST" "$@"
  else
    aws iam create-role --role-name "$NAME" --assume-role-policy-document "$TRUST" \
      --description "GitHub Actions deploys the $STACK stack (deploy.sh)" --max-session-duration 3600 "$@" >/dev/null
  fi
  aws iam put-role-policy --role-name "$NAME" --policy-name deploy --policy-document "$POLICY" "$@"
  ROLE_ARN="$(aws iam get-role --role-name "$NAME" --query Role.Arn --output text "$@")"
  echo "role $NAME, usable only from pushes to main of $REPO"
  VARS=("CI_DEPLOY=on" "AWS_REGION=$REGION" "AWS_DEPLOY_ROLE=$ROLE_ARN")
else
  if ! aws iam get-user --user-name "$NAME" "$@" >/dev/null 2>&1; then
    OUT="$(aws iam create-user --user-name "$NAME" --tags Key=purpose,Value="GitHub Actions deploys $STACK" "$@" 2>&1)" || {
      echo "$OUT"; echo
      echo "This account allows neither an OIDC provider nor an IAM user, so GitHub cannot deploy into it."
      echo "Deploying stays a local step: cd into the repository and run  git pull && ./deploy.sh $*"
      exit 1
    }
  fi
  aws iam put-user-policy --user-name "$NAME" --policy-name deploy --policy-document "$POLICY" "$@"
  # One fresh key each run; older keys of this user are retired so a leaked one cannot linger.
  for old in $(aws iam list-access-keys --user-name "$NAME" --query 'AccessKeyMetadata[].AccessKeyId' --output text "$@"); do
    aws iam delete-access-key --user-name "$NAME" --access-key-id "$old" "$@"
  done
  read -r AKID SECRET <<< "$(aws iam create-access-key --user-name "$NAME" --query '[AccessKey.AccessKeyId,AccessKey.SecretAccessKey]' --output text "$@")"
  echo "user $NAME with a new access key (any older key of this user was retired)"
  VARS=("CI_DEPLOY=on" "AWS_REGION=$REGION" "AWS_DEPLOY_ROLE=")
  SECRETS=("AWS_ACCESS_KEY_ID=$AKID" "AWS_SECRET_ACCESS_KEY=$SECRET")
fi
finish
