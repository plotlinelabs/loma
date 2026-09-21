# 05: Fixture + demo reproduction

**What to build:** A re-runnable script that populates the Library through the **real**
registration seam, so a reviewer can demo the feature without a live model. It creates a
source conversation and a few Assets (pdf, png, csv), plus **one deliberately unavailable**
Asset (registered, then its bytes removed) to demonstrate the degraded state. It ships the
documented sample prompt that reproduces a real dual-file turn — the same prompt reused as
a test input.

Read first: `trial/asset-library/DESIGN.md` (Fixture, Further Notes). This ticket only
needs the recording seam, so it can run as soon as ticket 01 lands and gives tickets 02–04
data to demo against.

**Blocked by:** 01 (Asset store + recording seam).

**Status:** ready-for-agent

- [ ] Running the script creates a conversation and registers files through the real seam,
      so the Assets appear in the Library for their owner.
- [ ] It includes exactly one unavailable Asset (registered, bytes removed) to exercise the
      degraded state from ticket 04.
- [ ] It is idempotent — a second run neither duplicates Assets nor errors.
- [ ] It documents how to run it and includes the sample dual-file prompt.

**Testing plan:**

- A fixture run populates the expected number of Assets, including exactly one unavailable
  item; a second run leaves the set unchanged (idempotent).
