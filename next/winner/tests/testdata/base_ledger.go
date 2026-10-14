// Package ledger keeps a running account of entries and prints statements.
package ledger

import (
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"
)

// Entry is one line of the ledger.
type Entry struct {
	When   time.Time
	Amount int64 // cents
	Memo   string
	Tags   []string
}

// Ledger holds entries in the order they were added.
type Ledger struct {
	entries []Entry
}

// ErrEmptyMemo is returned when an entry has no memo.
var ErrEmptyMemo = errors.New("ledger: entry needs a memo")

// Add appends an entry.
func (l *Ledger) Add(e Entry) error {
	if strings.TrimSpace(e.Memo) == "" {
		return ErrEmptyMemo
	}
	l.entries = append(l.entries, e)
	return nil
}

// Balance is the sum of all amounts.
func (l *Ledger) Balance() int64 {
	var total int64
	for _, e := range l.entries {
		total += e.Amount
	}
	return total
}

// Between returns the entries in [from, to), oldest first.
func (l *Ledger) Between(from, to time.Time) []Entry {
	var out []Entry
	for _, e := range l.entries {
		if !e.When.Before(from) && e.When.Before(to) {
			out = append(out, e)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].When.Before(out[j].When) })
	return out
}

// Statement prints the entries in a month, one per line, with a total.
func (l *Ledger) Statement(year int, month time.Month) string {
	from := time.Date(year, month, 1, 0, 0, 0, 0, time.UTC)
	to := from.AddDate(0, 1, 0)
	var b strings.Builder
	var total int64
	for _, e := range l.Between(from, to) {
		fmt.Fprintf(&b, "%s  %10s  %s\n", e.When.Format("2006-01-02"), cents(e.Amount), e.Memo)
		total += e.Amount
	}
	fmt.Fprintf(&b, "%s  %10s  total\n", strings.Repeat(" ", 10), cents(total))
	return b.String()
}

func cents(v int64) string {
	sign := ""
	if v < 0 {
		sign, v = "-", -v
	}
	return fmt.Sprintf("%s%d.%02d", sign, v/100, v%100)
}
