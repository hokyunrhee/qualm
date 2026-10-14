"""Everything e-spot says to Jev and to the coding agent, in one place.

Jev gets one request per changed unit (a function, a method, or the file's module-level code).
The state describes the unit; the questions are the five verbs reviewers use when they ask for a
change to be simpler, plus the overall question. Each question is a `noul` (a probability that the
answer is yes). The core client appends a guard sentence to every instruction.

The finding templates turn a high verb answer into what the coding agent reads: what is wrong
and what to do, with a "done when" so the agent knows when it has finished. Templates name only
line numbers and unit names, never text from the change's comments or strings.
"""

# How to read the state. Sent with every request.
STATE_FORMAT = (
    "One unit of a file that a pull request changed: a function or method, or the file's code outside any function. "
    "`later` is the unit after the change. Each line starts with its line number in the new file; lines marked + were "
    "added or changed by the pull request, the others are unchanged context. `earlier` is the same unit before the change, "
    "numbered in the old file, with the lines the change removed marked -; it is absent when the unit is new. `outline` "
    "lists the file's functions with their line ranges (+ new, * changed in this pull request). `similar`, when present, "
    "is the code elsewhere in the file that looks most like this unit. Comments and long string literals were replaced "
    "by placeholders; lines that are not shown are marked with an ellipsis."
)

# The five verbs, in the order reviewers reach for them, then the overall question.
QUESTIONS = {
    'remove': {
        'type': 'noul',
        'instructions': (
            "Do the lines marked + include code that could be deleted without changing what the program does: a variable, "
            "parameter, option or import that is never used, a step that repeats work already done, a branch that can never "
            "run, or a check or fallback for a state the surrounding code shows cannot happen?"
        ),
    },
    'reuse': {
        'type': 'noul',
        'instructions': (
            "Do the lines marked + re-implement something that already exists, either a function listed in the outline or "
            "a standard-library or built-in language feature, so that a reviewer would ask for the existing one to be "
            "called instead?"
        ),
    },
    'merge': {
        'type': 'noul',
        'instructions': (
            "Do the lines marked + repeat logic that appears elsewhere in this unit or in the file nearly line for line, or "
            "add a second code path for a case an existing path already handles, so that a reviewer would ask for one "
            "shared version?"
        ),
    },
    'shorten': {
        'type': 'noul',
        'instructions': (
            "Could the lines marked + say the same thing in noticeably fewer lines with a simpler construct, such as a "
            "built-in idiom, a direct expression instead of a temporary variable, an early return instead of nesting, or "
            "one condition instead of several?"
        ),
    },
    'split': {
        'type': 'noul',
        'instructions': (
            "After this change, is the unit too long or does it do several separate jobs, so that a reviewer would ask for "
            "part of the lines marked + to move into a function of its own?"
        ),
    },
    'push_back': {
        'type': 'noul',
        'instructions': (
            "Would an experienced reviewer ask for the lines marked + in this unit to be simplified before the pull request "
            "merges? A change that is already about as simple as its job allows is a no."
        ),
    },
}

VERBS = ('remove', 'reuse', 'merge', 'shorten', 'split')
IDS = VERBS + ('push_back',)

# What the coding agent reads when a unit's answer is high. {span} is "lines 40-52 (in parseConfig)",
# {where} is "the added lines of parseConfig" or "the added module-level lines", {unit} the unit's
# name, {related} a pointer to the other copy when one is known (or empty).
FINDINGS = {
    'remove': {
        'message': "At {span} the change adds code that can likely be deleted without changing behaviour: an unused value or "
                   "parameter, a repeated step, a branch that cannot run, or a check for a state the surrounding code rules out.",
        'fix': "Find the statements among {where} whose removal leaves behaviour and tests unchanged, and delete them; if a check "
               "guards a case that can really happen, keep it and say so in the code. Done when every added line in {unit} is "
               "needed for the result.",
    },
    'reuse': {
        'message': "At {span} the change re-implements something this file or the standard library already provides.",
        'fix': "Replace the hand-written steps among {where} with a call to the existing helper in this file or the "
               "standard-library function that does the same job, and delete what it replaces. Done when {unit} calls the "
               "existing code instead of repeating it.",
    },
    'merge': {
        'message': "At {span} the change repeats logic found elsewhere in the file{related}, or adds a second path for a case an "
                   "existing path already handles.",
        'fix': "Keep one version: move the shared steps into one function (or route this case through the existing path) and "
               "call it from both places, passing what differs as a parameter. Done when the logic exists once.",
    },
    'shorten': {
        'message': "At {span} the change takes noticeably more lines than it needs; a simpler construct says the same thing.",
        'fix': "Rewrite {where} with the simplest construct that keeps the behaviour: use values directly instead of through "
               "temporaries, use the language's idiom or a built-in, return early instead of nesting, combine conditions. "
               "Done when the added lines in {unit} are clearly fewer and the tests still pass.",
    },
    'split': {
        'message': "At {span} the change leaves {unit} long or doing several separate jobs.",
        'fix': "Move the new block of work in {unit} into a function of its own whose name says what it does, and call it from "
               "{unit}. Done when {unit} does one job and is no longer than it was before the change (a new function: under "
               "about 35 lines).",
    },
    'push_back': {
        'message': "At {span} a reviewer would likely ask for the change to be simplified, though no single kind of "
                   "simplification stands out.",
        'fix': "Re-read {where} and cut what the feature does not need: duplicated steps, unused values, checks for impossible "
               "states, needless temporaries. Done when every added line in {unit} is needed and no shorter version would "
               "be as clear.",
    },
}
