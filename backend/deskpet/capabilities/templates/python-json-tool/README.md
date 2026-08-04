# Python JSON tool template

This template implements `deskpet-json-tool-v1`. It reads exactly one JSON
request from stdin and writes exactly one JSON object to stdout.

Generated tools must use `brokered-effect-v1`: compute an `effect_plan` from
opaque input views and let the DeskPet host validate and execute every side
effect. Generated code must not receive host paths or mutate the OS directly.

Run locally:

```powershell
Get-Content request.json | python main.py
```
