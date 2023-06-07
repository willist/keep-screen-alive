## Summary
A simple command line wrapper for [caffeinate](https://ss64.com/osx/caffeinate.html) on macOS that provides a forward looking relative datetime interface.

## Examples

```bash
$ date
Wed Jun  1 08:00:00 CDT 2023

$ keep-alive 2h
Keeping alive until 10:00AM CDT, Jun 01, 2023

$ keep-alive 12pm
Keeping alive until 12:00PM CDT, Jun 01, 2023

$ keep-alive 7am
Keeping alive until 07:00AM CDT, Jun 02, 2023
```
