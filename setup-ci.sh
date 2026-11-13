#!/usr/bin/env bash
# One-time: let GitHub Actions deploy this repository into your AWS account, with no long-lived keys anywhere.
# GitHub signs each run with OIDC; AWS trusts those tokens only for pushes to main of the one repository below.
#
#   ./setup-ci.sh --profile santa --region us-east-2
#
# Afterwards, in the repository on GitHub (Settings > Secrets and variables > Actions):
#   Variables: AWS_DEPLOY_ROLE = the arn printed at the end,  AWS_REGION = us-east-2
#   Secrets (optional): ALERT_EMAIL = where the "API is throwing errors" email should go
# Safe to run again: everything is created only if missing, and the policies are brought up to date.
set -euo pipefail
cd "$(dirname "$0")"

REPO="${REPO:-tongerlee/SecretSanta}"
ROLE="${ROLE:-github-deploy-secret-santa}"
STACK="${STACK:-secret-santa}"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text "$@")"
PARTITION="$(aws sts get-caller-identity --query Arn --output text "$@" | cut -d: -f2)"
PROVIDER="arn:${PARTITION}:iam::${ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"

echo "== 1/3  GitHub as an identity provider in account $ACCOUNT"
if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$PROVIDER" "$@" >/dev/null 2>&1; then
  echo "already there"
else
  # AWS now checks GitHub's certificate chain itself; the thumbprint is still a required argument.
  aws iam create-open-id-connect-provider --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 "$@" >/dev/null
  echo "created"
fi

echo "== 2/3  Role $ROLE, usable only from pushes to main of $REPO"
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
if aws iam get-role --role-name "$ROLE" "$@" >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name "$ROLE" --policy-document "$TRUST" "$@"
  echo "already there; trust policy refreshed"
else
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document "$TRUST" \
    --description "GitHub Actions deploys the $STACK stack (deploy.sh)" --max-session-duration 3600 "$@" >/dev/null
  echo "created"
fi

echo "== 3/3  What deploy.sh is allowed to do"
# Everything deploy.sh touches: the CloudFormation stack and SAM's upload bucket, the Lambda, its role, the table,
# the Amplify app, the alarms, plus reading the stack outputs. Scoped to this stack's names where the service allows it.
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
aws iam put-role-policy --role-name "$ROLE" --policy-name deploy --policy-document "$POLICY" "$@"
ROLE_ARN="$(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text "$@")"

echo
echo "Done. Now tell GitHub about the role, either with the gh CLI:"
echo
echo "  gh variable set AWS_DEPLOY_ROLE --repo $REPO --body \"$ROLE_ARN\""
echo "  gh variable set AWS_REGION --repo $REPO --body \"${AWS_REGION:-us-east-2}\""
echo "  gh secret set ALERT_EMAIL --repo $REPO            # optional, type your email when asked"
echo
echo "or on github.com: $REPO > Settings > Secrets and variables > Actions."
echo "From then on every push to main runs the tests and deploys (Actions tab shows each run)."
