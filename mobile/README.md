# Mobile

**Status:** planned for Step 4. Framework recommendation awaiting confirmation.

Three apps are needed: customer, courier and merchant.

**Recommendation: React Native with Expo**, over Flutter. Not because it is technically superior,
but because it shares TypeScript, the generated API client and the domain vocabulary with the web
app. One language across four surfaces is a hiring and velocity argument.

Flutter would win on raw rendering performance for the courier's live map, and that is worth
revisiting if map performance becomes the binding constraint.

Rationale in [repository structure](../docs/architecture/01-repo-structure.md#frontend).
