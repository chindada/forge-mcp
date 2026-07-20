# Tiny Feature: Hello Forge

## Goal

Create a file `out.txt` in the target directory containing exactly the line `hello forge`.

## Plan

1. Write `out.txt` with the content `hello forge` followed by a newline.

## Verification

```bash
test -f out.txt && grep -q 'hello forge' out.txt
```

The verification passes when `out.txt` exists and contains the string `hello forge`.
