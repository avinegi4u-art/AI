# Notification service

**Status:** planned for Step 4. Not yet implemented.

**Bounded context:** the `notifications` schema.

Push, SMS and email, driven entirely by events. Owns templates, delivery attempts and user
channel preferences.

Takes over real OTP delivery from the auth service's pluggable sender, whose interface is
already in place (`marsool_auth.otp.OtpSender`).

This directory exists so the service catalog in the documentation matches the repository, and so
the eventual implementation has an obvious home. It will follow the same internal shape as every
implemented service — see
[repository structure](../../docs/architecture/01-repo-structure.md#inside-a-service).
