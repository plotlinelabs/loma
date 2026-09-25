# Asset Library — Context (Glossary)

Domain vocabulary for the Asset Library trial. One term per entry, definitions only —
no implementation. When a term below conflicts with how a word is used elsewhere,
this file wins.

- **Asset** — A file-backed output a user can find in one place, open or download, and
  trace back to the conversation that produced it. Grained **one per file**: a single
  turn that emits two files yields two Assets sharing one source conversation.

- **File-backed output** — An output whose substance is a real file with bytes,
  reachable through the application's existing file-serving path behind an owner check.
  Distinct from an output whose substance is inline text.

- **Live file chip** — The in-conversation affordance offering a just-produced file for
  download. Evidence that a file was produced; not a durable record of it.

- **File artifact** — A conversation output recorded as pointing to a generated file.
  Some producers record this; others only surface a chip.

- **Code artifact** — A conversation output whose substance is inline text or code. **Not
  an Asset** — the library concerns file-backed outputs only.

- **Owner** — The user who generated a file, and the sole party permitted to see or open
  its Asset. Ownership does not transfer in this scope; there is no sharing.

- **Source conversation** — The conversation an Asset was produced in; the destination
  when a user asks to return to where an output came from.

- **Availability** — Whether an Asset's bytes can currently be opened. An Asset whose
  record persists but whose bytes are gone is **unavailable**, and reports the reason it
  became so.

- **Provider** — A means by which the platform produces files (OpenCode, Claude, and
  others). The library treats every provider uniformly: any provider that produces a
  file-backed output contributes Assets without the library knowing which one produced it.

- **Registration** — The act of taking a produced file and making it an owned,
  serve-able output. The moment an Asset comes into being.

- **Fixture** — A repeatable script that produces sample Assets through the real
  registration path, so the library can be demonstrated without a live model turn.
