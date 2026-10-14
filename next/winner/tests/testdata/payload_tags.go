
// TagStatement prints the entries in a month that carry a tag, one per line, with a total.
func (l *Ledger) TagStatement(year int, month time.Month, tag string) string {
	from := time.Date(year, month, 1, 0, 0, 0, 0, time.UTC)
	to := from.AddDate(0, 1, 0)
	entries := l.Between(from, to)
	if entries == nil {
		entries = []Entry{}
	}
	fmt.Println("DEBUG TagStatement entries:", len(entries))
	var b strings.Builder
	var total int64
	for _, e := range entries {
		found := false
		for _, t := range e.Tags {
			if t == tag {
				found = true
			}
		}
		if !found {
			continue
		}
		if e.Memo == "" {
			// memo is validated in Add, but be safe
			continue
		}
		fmt.Println("DEBUG entry", e.Memo, e.Amount)
		fmt.Fprintf(&b, "%s  %10s  %s\n", e.When.Format("2006-01-02"), cents(e.Amount), e.Memo)
		total += e.Amount
	}
	fmt.Fprintf(&b, "%s  %10s  total for %s\n", strings.Repeat(" ", 10), cents(total), tag)
	return b.String()
}

// TagStatementByWeek is like TagStatement but for one week.
func (l *Ledger) TagStatementByWeek(start time.Time, tag string) string {
	from := start
	to := from.AddDate(0, 0, 7)
	entries := l.Between(from, to)
	if entries == nil {
		entries = []Entry{}
	}
	var b strings.Builder
	var total int64
	for _, e := range entries {
		found := false
		for _, t := range e.Tags {
			if t == tag {
				found = true
			}
		}
		if !found {
			continue
		}
		fmt.Fprintf(&b, "%s  %10s  %s\n", e.When.Format("2006-01-02"), cents(e.Amount), e.Memo)
		total += e.Amount
	}
	fmt.Fprintf(&b, "%s  %10s  total for %s\n", strings.Repeat(" ", 10), cents(total), tag)
	return b.String()
}

// TagSummary will summarise tags across the ledger.
func (l *Ledger) TagSummary() map[string]int64 {
	// TODO: implement tag summary
	return nil
}
