
// TagStatement prints the entries in a month that carry tag, one per line, with a total.
func (l *Ledger) TagStatement(year int, month time.Month, tag string) string {
	from := time.Date(year, month, 1, 0, 0, 0, 0, time.UTC)
	return l.statement(l.tagged(l.Between(from, from.AddDate(0, 1, 0)), tag), "total for "+tag)
}

func (l *Ledger) tagged(entries []Entry, tag string) []Entry {
	var out []Entry
	for _, e := range entries {
		if hasTag(e, tag) {
			out = append(out, e)
		}
	}
	return out
}

func hasTag(e Entry, tag string) bool {
	for _, t := range e.Tags {
		if t == tag {
			return true
		}
	}
	return false
}
