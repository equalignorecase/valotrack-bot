# Help Embed Design

- **Intent:** Make bot embeds easier to scan and less emoji-heavy.
- **Goals:** Keep `/help` commands grouped under short headings with one-line descriptions and 2–3 short `/login` steps; remove decorative emoji from embeds.
- **Non-goals:** Add pagination or buttons; change command behavior or remove emoji from UI elements outside embeds.
- **Approaches:** Keep full command list with one-line descriptions (chosen for discoverability); show command names only (shortest, but unclear); show popular commands only (short, but incomplete).
- **Constraints:** Keep the existing single ephemeral `/help` embed, Thai copy, Owner-only label, and semantic status/result wording.
- **Builder calls:** Remove decorative emoji from embed titles, fields, and descriptions; spell out statuses and match results in text. Retain branding colors and images.
