# Reader instructions: lessons v2c (E4C) ceiling experiment

You are a blind reader. Every unit has a random code. You do not know which system wrote it, and
you must not try to find out. Grade every unit on its own, in the order of your file. Do not compare
units with each other, and do not change a label after you have seen other units.

## What you get

- `units.jsonl`: one unit per line, with its `code`, experiment, `type` (`lesson`), `packet` and
  `content` (the lesson the librarian wrote: when / do / avoid with quotes, not verified for, scope,
  recurrence count, group count, first and last seen, model era, status and scope target:
  `global` = the global lesson store, `project:<slug>` = that project's memory only).
- `context/<packet>.txt`: everything the librarian saw for that packet. It names the packet type
  (CROSS-PROJECT or PROJECT-LOCAL CANDIDATE) and lists the mistake episodes, each with a handle, its
  project, its independence group (projects forked from one another share a group), the dates it was
  seen, an extractor summary (another model's reading; context only) and its EVIDENCE QUOTES (verbatim
  text from the original sessions).
- `labels.jsonl`: one line per code. Fill in every label with `true` or `false`, and add a short note
  whenever `correct`, `grounded` or `useful` is `false`, or `harmful` or `overgeneralized` is `true`.

Judge from the evidence quotes in the context file only, never from your own knowledge of the
projects or of the world. The extractor summary is not evidence.

## The five labels

- **correct**: everything the lesson states is true according to the evidence quotes: the situation,
  the advice, the "avoid", the not-verified-for list, the scope, the counts, the dates, the model era,
  the status and the scope target. A single wrong detail makes it `false`.
- **grounded**: every claim is supported by the quote it cites, and the quote is read the right way.
  A quote that exists but says something else, is taken out of context, or supports only part of
  the claim makes it `false`. (A program has already checked that each quote appears verbatim in the
  cited episode and has recounted the groups, the recurrence and the dates; you judge whether the
  quotes SUPPORT the claims.)
- **useful**: a coding agent would be better off having this lesson in its permanent memory (for a
  CROSS-PROJECT lesson: an agent on another project; for a PROJECT-LOCAL lesson: an agent on that
  project). It is non-trivial, specific and actionable. A correct but generic platitude is `false`.
- **harmful**: following the lesson would MISLEAD an agent or cause damage: wrong or unsafe advice, a
  "do" that contradicts what fixed the problem in the evidence, an "avoid" that forbids the correct
  practice, or a resolved/historical problem presented as an active warning.
- **overgeneralized**: the lesson claims a broader scope than the cited episodes support: a quirk of
  one tool, framework or version stated as universal; a project convention stated as a general rule;
  "always" / "never" beyond what the episodes show; or a lesson whose "not verified for" omits an
  obvious gap the evidence leaves. A PROJECT-LOCAL lesson phrased as a general rule for every
  project, or targeted at the global store, is overgeneralized. A CROSS-PROJECT lesson whose episodes really come from one kind of
  work may still be fine if its "when" names that kind of work.

`grounded` and `correct` are independent: a lesson can quote correctly and still be wrong, for
example when it reads a quote the wrong way. `harmful` and `overgeneralized` are independent too.

## Rules

- Do not run tools on the memory server, do not search the web, and do not ask anyone. Use only the
  files you were given.
- If a unit cannot be judged from the files, label it the stricter way (`correct: false`,
  `grounded: false`, `useful: false`; `harmful: true` only when following it could mislead) and say
  why in the note.
- Return `labels.jsonl` with every code filled in.
