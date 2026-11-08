# Help Embed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make bot embeds easier to scan with less decorative emoji.

**Architecture:** Edit existing embed copy in `cogs/general.py`, `cogs/auth.py`, `cogs/shop.py`, `cogs/live.py`, and `cogs/stats.py`; add no helpers or dependencies.

**Tech Stack:** Python, discord.py app commands.

**Spec:** `docs/superpowers/specs/2026-11-08-help-embed-design.md`

## Global Constraints

- Remove decorative emoji from embeds, including dynamic results and status labels.
- Keep textual status and match-result information clear.
- Keep `/help` commands, short headings, one-line descriptions, 2–3 short `/login` steps, and the Owner-only label.
- Keep branding colors and images.
- Leave button labels, select menus, and non-embed messages unchanged.
- Do not change command behavior.

## Review Focus

- Confirm no command is omitted or renamed.
- Confirm `/account-list` remains labeled Owner-only.
- Confirm `/help` remains ephemeral.
- Keep the help text within Discord embed limits.

---

### Task 1: Reduce emoji in embeds

**Files:**
- Modify: `cogs/general.py`
- Modify: `cogs/auth.py`
- Modify: `cogs/shop.py`
- Modify: `cogs/live.py`
- Modify: `cogs/stats.py`

**Steps:**
- [x] Replace verbose command explanations with one-line descriptions.
- [x] Keep concise `/login` setup guidance; remove the standalone security section.
- [x] Remove decorative emoji from embed text and retain clear text labels.
- [x] Keep all commands, category groupings, Owner-only label, and ephemeral response.
- [x] Review the diff and run `git diff --check`.
