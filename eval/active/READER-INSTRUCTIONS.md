# Reader instructions: active librarian ceiling experiment

You are a blind reader. Every unit has a random code. You do not know which system wrote it, and
you must not try to find out. Grade every unit on its own, in the order of your file. Do not compare
units with each other, and do not change a label after you have seen other units.

## What you get

- `units.jsonl`: one unit per line, with its `code`, experiment, `type`, `hiding` flag, `packet` and
  `content` (what the librarian produced).
- `context/<packet>.txt`: everything the librarian saw for that packet (its sources, each with a
  handle such as `v123`). For session distillation (E1) the file also lists the NEWER session notes
  of the same project, which the librarian did not see. Use them to judge whether a decision was
  reversed later.
- `labels.jsonl`: one line per code. Fill in every label with `true` or `false`, and add a short note
  whenever a label is `false` (for `harmful_stale`, whenever it is `true`).

Read the cited sources in the context file. Judge from the sources only, never from your own
knowledge of the project or of the world.

## The four labels (E1, E2, E3)

- **correct**: everything the unit states is true according to the sources, including dates,
  names, numbers and the direction of every relation. A single wrong detail makes it `false`. A
  statement that was true once but is no longer current counts as wrong when the unit presents it
  as current.
- **grounded**: every claim is supported by the quote it cites, and the quote is read the right way.
  A quote that exists but says something else, is taken out of context, or supports only part of
  the claim makes it `false`. (A program has already checked that each quote appears verbatim; you
  judge whether it SUPPORTS the claim.)
- **useful**: a coding agent starting work on this project would be better off having this unit in
  its memory. It is non-trivial, specific and actionable, and it is not just a restatement of an
  item that already exists. A correct but pointless unit is `false`.
- **harmful_stale**: the unit would MISLEAD an agent. It presents as current something that is
  outdated, superseded or reversed, or it promotes something unsettled (a proposal, an open
  question, an unverified result) as settled. For a unit with `hiding: true` (a supersession, a
  merge or a close), it is `true` when applying the unit would hide, close or mark as outdated an
  item that is still current, wholly or in part.

`grounded` and `correct` are independent: a unit can quote correctly and still be wrong, for
example when it reads a quote the wrong way.

## Unit types

- `fact` (E1): a fact derived from a session note's Decisions lines. Check that it really was
  decided (and not only proposed or listed as uncertain), and that a newer note did not reverse it.
- `supersede` (E1, hiding): the claim that a new fact makes an existing item (`target`) outdated.
  Check both the fact and that the quoted target statement really is replaced.
- `card_line` (E2): one line of a project card ("now" or "decisions"). The whole card is shown for
  context. Judge the line.
- `merge` (E2, hiding): a proposal that items state the same thing and can become one item.
  `harmful_stale` is `true` when the absorbed item carries current information that the kept item
  lacks.
- `experience` (E3): a cross-project experience (when / do / avoid). `useful` asks whether it is a
  real cross-project pattern that an agent can apply. `correct` asks whether every line follows from
  the quoted lessons, without added advice.

## Coverage (E2 only)

`coverage.jsonl` lists the cards, each with a project's must-know facts. These facts were written
before any card existed. For each fact, mark `covered: true` when the card states it, or an
equivalent statement, as current. Mark `false` when the card misses it or contradicts it.

## Proposals (E0 only)

Each unit is one proposal of the current observer librarian: a relation between two items
(duplicate, refines, relates, contradicts, supersedes), with its reason, its quotes and the actions
it would apply. Use only two labels:

- **correct**: the proposed relation and its direction are right, and its actions are what the
  owner should apply.
- **harmful_stale**: applying the proposal would hide, close or mark as outdated an item, or a
  statement in it, that is still current (a false invalidation).

Leave `grounded` and `useful` as `null` for E0.

## Rules

- Do not run tools on the memory server, do not search the web, and do not ask anyone. Use only the
  files you were given.
- If a unit cannot be judged from the files, label it the stricter way (`correct: false`,
  `harmful_stale: true` when it would hide something) and say why in the note.
- Return `labels.jsonl` (and `coverage.jsonl` for E2) with every code filled in.
