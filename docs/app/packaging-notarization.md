# macOS Packaging, Signing, And Notarization

The first app shell is a SwiftPM app staged as `ORAM.app`. Public distribution
outside the Mac App Store should use Developer ID signing and Apple
notarization.

## Unsigned Development Build

```bash
apps/macos/script/package_unsigned.sh
```

Outputs:

```text
apps/macos/dist/ORAM.app
apps/macos/dist/ORAM.dmg
apps/macos/dist/checksums.txt
releases/macos/ORAM.dmg
releases/macos/checksums.txt
```

The `releases/macos` copy is intentionally tracked so the repository contains a
downloadable development DMG.

The development DMG bundles the ORAM Python source, lockfile, and a `uv` helper
under `ORAM.app/Contents/Resources`. The app uses that helper to create the
runtime environment under `~/Library/Application Support/ORAM`. A future fully
offline package should replace first-run dependency resolution with an embedded
Python runtime or a frozen daemon binary.

## Developer ID Signing

Public distribution requires a Developer ID Application certificate. The local
development package is ad-hoc signed and should not be shipped as the public
download.

Confirm your signing identity:

```bash
security find-identity -p codesigning -v
```

Then set:

```bash
export ORAM_DEVELOPER_ID_APP="Developer ID Application: ..."
export ORAM_NOTARY_PROFILE="oram-notary"
```

Create a signed DMG:

```bash
apps/macos/script/package_signed.sh
```

The script builds `apps/macos/dist/ORAM.app`, signs the nested `uv` helper,
signs the app with hardened runtime and a secure timestamp, creates
`apps/macos/dist/ORAM-signed.dmg`, signs the DMG, and notarizes/staples it when
`ORAM_NOTARY_PROFILE` is set.

Use `ORAM_COPY_RELEASES=1 apps/macos/script/package_signed.sh` only when you
intentionally want to copy the signed DMG into `releases/macos`.

## Notarization Setup

Create the notarytool keychain profile once:

```bash
xcrun notarytool store-credentials "$ORAM_NOTARY_PROFILE" \
  --apple-id "you@example.com" \
  --team-id "TEAMID" \
  --password "app-specific-password"
```

Manual notarization commands, if needed:

```bash
xcrun notarytool submit apps/macos/dist/ORAM-signed.dmg \
  --keychain-profile "$ORAM_NOTARY_PROFILE" \
  --wait
xcrun stapler staple apps/macos/dist/ORAM-signed.dmg
xcrun stapler validate apps/macos/dist/ORAM-signed.dmg
```

## Do You Need Apple?

For local testing and repository development builds: no. An unsigned DMG can be
stored in the repository and opened manually.

For public distribution with a normal first-launch experience: yes. Join the
Apple Developer Program, create a Developer ID Application certificate, sign the
app with hardened runtime, submit the DMG to Apple notarization, and staple the
notarization ticket.

## Secret Exclusion

Packaging scripts must exclude:

- `.env`
- `.env.*`
- local Keychain exports
- daemon metadata files containing local control tokens
- ORAM Library content unless building an explicit fixture package
