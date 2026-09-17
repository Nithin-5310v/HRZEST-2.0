# Mobile Android Build via AWS CodeBuild (alternative to EAS Build)

Builds `mobile/`'s signed release `.aab` using AWS CodeBuild instead of
Expo's EAS Build, since EAS Build/`eas submit` both require an interactive
browser login that couldn't be completed non-interactively during initial
setup. **This replaces only the build step** — Google Play Store is still
the only distribution channel; you'll still upload the resulting `.aab` to
Play Console yourself (or wire that up separately later).

Infrastructure: `terraform/mobile_codebuild.tf`. Build logic:
`mobile/buildspec-android.yml` + `mobile/scripts/patch-android-release-signing.js`.

## What's already done

- An Android upload keystore has been generated for you:
  `hrzest-upload-key.keystore` (PKCS12, alias `hrzest-upload`), currently
  sitting in this session's scratch directory along with its password in
  `hrzest-upload-key.passwords.txt`. **Move both files somewhere safe you
  control (a password manager, an encrypted drive) before this session
  ends — they will not persist otherwise.** Losing this keystore before
  Play Store's "Play App Signing" is enrolled means losing the ability to
  publish updates under the same app identity.
- `terraform/mobile_codebuild.tf` provisions: an S3 bucket for build
  output, a Secrets Manager secret (empty placeholder) for the keystore,
  an IAM role scoped to just that secret + that bucket, and the CodeBuild
  project itself.

## One-time manual setup (required before the first build)

### 1. Create a GitHub Personal Access Token

CodeBuild needs this to clone the repo. Go to
[github.com/settings/tokens](https://github.com/settings/tokens) →
**Generate new token (classic)** → scope: `repo` → generate, copy it.

```bash
export TF_VAR_github_pat="ghp_xxxxxxxxxxxxxxxxxxxx"
```

If this AWS account/region already has a GitHub source credential
registered (console: CodeBuild → Settings → GitHub, or a prior
`aws codebuild import-source-credentials`), **remove `aws_codebuild_source_credential.github`
from `terraform/mobile_codebuild.tf`** first — CodeBuild allows only one
per account/region and `terraform apply` will fail trying to create a
second.

### 2. Apply the Terraform

```bash
cd terraform
terraform apply -target=aws_s3_bucket.mobile_build_artifacts \
  -target=aws_secretsmanager_secret.mobile_android_signing \
  -target=aws_iam_role.codebuild_android_role \
  -target=aws_iam_role_policy.codebuild_android_policy \
  -target=aws_codebuild_source_credential.github \
  -target=aws_codebuild_project.mobile_android_build
```

(Targeted apply so this doesn't also try to reconcile every other
already-applied resource in this same `terraform/` directory in one shot —
drop the `-target` flags for a normal full apply once you're comfortable
this is the only pending change.)

### 3. Populate the signing secret

Using the keystore generated for you:

```bash
KEYSTORE_PATH="/path/to/hrzest-upload-key.keystore"   # wherever you moved it
STORE_PASSWORD="<from hrzest-upload-key.passwords.txt>"

python3 -c "
import json, base64
payload = {
    'KEYSTORE_BASE64': base64.b64encode(open('$KEYSTORE_PATH','rb').read()).decode(),
    'KEYSTORE_STORE_PASSWORD': '$STORE_PASSWORD',
    'KEYSTORE_KEY_PASSWORD': '$STORE_PASSWORD',
    'KEYSTORE_KEY_ALIAS': 'hrzest-upload',
}
open('/tmp/keystore-secret-payload.json','w').write(json.dumps(payload))
"

aws secretsmanager put-secret-value \
  --secret-id "$(terraform -chdir=terraform output -raw mobile_android_signing_secret_arn)" \
  --secret-string file:///tmp/keystore-secret-payload.json

rm -f /tmp/keystore-secret-payload.json   # don't leave plaintext secrets on disk
```

### 4. Trigger a build

```bash
aws codebuild start-build \
  --project-name "$(terraform -chdir=terraform output -raw mobile_android_codebuild_project)"
```

Watch it in the CodeBuild console, or:
```bash
aws codebuild batch-get-builds --ids <build-id-from-start-build-output>
```

A full build (first run especially — Android SDK install + Gradle's first
cold build) can take 15–25 minutes.

### 5. Download the AAB

```bash
aws s3 ls "s3://$(terraform -chdir=terraform output -raw mobile_build_artifacts_bucket)/"
aws s3 cp "s3://<bucket>/<build-id>/build-output.zip" ./build-output.zip
unzip build-output.zip
```

The `.aab` inside is what you upload to Play Console → Production (or
Internal Testing first, per the original launch plan's Step 7).

## Bumping the app version for a new build

Edit `mobile/app.json`'s `expo.version`, and Android's `versionCode` is
handled by `eas.json`'s `autoIncrement` setting **only when building via
EAS** — this CodeBuild path does not auto-increment anything. Set
`android.versionCode` explicitly in `app.json` before each release build
here, incrementing it every time (Play Store rejects a re-upload with the
same versionCode).

## Known limitations of this path vs. EAS Build

- No automatic `versionCode` incrementing (see above) — manual discipline
  required.
- No `eas submit` equivalent — the AAB lands in S3, upload to Play Console
  is still a manual step (or build your own `aws s3 cp` + Play Developer
  API script later if this becomes frequent).
- Every build reinstalls the Android SDK from scratch (no custom CodeBuild
  image) — slower than EAS Build's warm build cache. Fine for infrequent
  releases; revisit with a custom image in ECR if this becomes painful.
- iOS is out of scope here entirely — CodeBuild's Linux environment can't
  build iOS; that still needs a Mac (EAS Build, or your own Mac + Xcode).
