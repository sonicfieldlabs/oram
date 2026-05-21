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

## Signing Placeholders

Future signed builds should provide:

```bash
export ORAM_DEVELOPER_ID_APP="Developer ID Application: ..."
export ORAM_DEVELOPER_ID_INSTALLER="Developer ID Installer: ..."
export ORAM_NOTARY_PROFILE="oram-notary"
```

Then sign:

```bash
codesign --force --deep --options runtime --sign "$ORAM_DEVELOPER_ID_APP" apps/macos/dist/ORAM.app
```

## Notarization Placeholder

```bash
xcrun notarytool submit apps/macos/dist/ORAM.dmg \
  --keychain-profile "$ORAM_NOTARY_PROFILE" \
  --wait
xcrun stapler staple apps/macos/dist/ORAM.dmg
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
