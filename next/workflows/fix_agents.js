export const meta = {
  name: 'qualm-next-fix-agents',
  description: 'Fix loop: one fresh coding agent per failing item, given only the candidate report',
  phases: [{ title: 'Fix', detail: 'one coding agent per (candidate, item, iteration)' }],
}
// args: {items: [{key, ws, base, report | report_file, test, iteration}]}: report_file holds the report text when it is too long to pass inline
// The prompt is identical for every candidate; only the report text and paths differ.
const SCHEMA = {
  type: 'object',
  properties: { summary: { type: 'string' }, tests_pass: { type: 'boolean' }, gave_up: { type: 'boolean' } },
  required: ['summary', 'tests_pass', 'gave_up'],
}
// args.sp, when given, replaces "$SP" in paths and commands (keeps the arguments short)
const ex = s => (args.sp && typeof s === 'string') ? s.split('$SP').join(args.sp) : s
const items = args.items.map(it => ({ ...it, ws: ex(it.ws), report_file: ex(it.report_file), test: ex(it.test) }))
phase('Fix')
const res = await parallel(items.map(it => () => agent(
`You are a coding agent. Your pull request lives in the git repository at ${it.ws}. Your change is the difference between commit ${it.base} and the current working tree (see it with: git -C ${it.ws} diff ${it.base}).

A required quality check on the pull request failed.${it.iteration > 1 ? ' This is your second attempt: your first rework is already in the working tree, and the check failed again.' : ''} Its report ${it.report_file ? 'is in the file named below' : 'is below'}. It is everything you know about why the check failed.

Rework the change so the check passes. The project's tests must still pass; run them with:
  cd ${it.ws} && ${it.test}

Work only inside ${it.ws}. Do not run git commit, git stash, git checkout or git reset: the harness records your edits from the working tree. When you are done, return a short summary of what you changed.

${it.report_file ? `The report is in the file ${it.report_file}; read it first (it is the only file outside your repository you may read).` : `REPORT
<<<
${it.report}
>>>`}`,
  { label: it.key, phase: 'Fix', schema: SCHEMA, effort: 'medium' }).then(r => r && ({ key: it.key, ...r }))))
return res.filter(Boolean)
