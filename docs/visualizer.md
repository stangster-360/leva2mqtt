# Visualizer.coffee setup

How to connect the bridge to [Visualizer.coffee](https://visualizer.coffee) and use
coffee bags and grinders. Background on the upload queue: [bridge.md](bridge.md#shot-delivery).

| Feature | Visualizer account | Bridge configuration | Home Assistant |
|---|---|---|---|
| Shot upload | free or Premium | `visualizer.username`, `visualizer.password` | switch **Upload shots to Visualizer** |
| Coffee bags | Premium, coffee management on | nothing extra (`visualizer.coffee_bags_refresh`) | select **Coffee bag**, button **Reload coffee bags** |
| Grinders | free or Premium | `shots.grinders` | select **Grinder** |
| Grind setting, dose, free-text bean | free or Premium | – | text **Grinder setting**, number **Dose**, text **Bean** |

## 1. Credentials

The bridge signs in with the e-mail address and password of your Visualizer account
(HTTP Basic Auth on the Visualizer API).

**Account with a passkey only:** Visualizer has no "set password" option in the
profile, but the reset flow works for every account:

1. Open <https://visualizer.coffee/passwords/new> and enter the account e-mail.
2. Open the link in the e-mail and set a password (at least 8 characters).
3. Visualizer signs you out everywhere; the passkey keeps working.

Then configure the bridge, either as environment variables

```sh
VISUALIZER_USERNAME=you@example.com
VISUALIZER_PASSWORD_FILE=/run/secrets/visualizer_password   # or VISUALIZER_PASSWORD=…
```

or in `config.yaml`:

```yaml
visualizer:
  username: you@example.com
  password: "…"
```

Check: at start the log line `leva2mqtt started: … outputs:` lists `visualizer`.

OAuth instead of a password is not supported yet.

## 2. Coffee bags (Premium)

1. In Visualizer: profile settings → switch on **coffee management**.
2. Create roasters and coffee bags in Visualizer (the AI import from a shop link works).
3. In Home Assistant press **Reload coffee bags**, or wait for the next automatic reload
   (at start and every `visualizer.coffee_bags_refresh` seconds, default 1800; `0`
   turns coffee bags off).
4. Pick the bag in **Coffee bag** before the shot.

What happens:

- Only active bags are listed; archived bags disappear and a selected one is cleared.
- Labels are `<coffee> – <roaster>`; two bags that would look the same get the roast
  date (or a short id) added.
- The bag selected when the shot **ends** is stored with the shot. After the
  upload the bridge links the shot to the bag; Visualizer then fills roaster, coffee,
  roast date and roast level from the bag. Log line on success:
  `…_shot.json: coffee_bag done`.
- **None** = no link; the free-text **Bean** is used instead.
- Without bags (free account, coffee management off) there is no **Coffee bag** entity.
  If a bag is selected but Visualizer does not take the link (coffee management off),
  the log says so and the shot stays uploaded without a bag.

Confirmed 13.10.2026: the bag list shows up in Home Assistant. The link itself is
**unverified** until the first linked shot.

## 3. Grinders

Visualizer has no grinder objects and no grinder API: it only collects the grinder
names of past shots as dropdown suggestions. So the bridge takes the grinders from its
own configuration:

```yaml
shots:
  grinders: ["Niche Zero", "DF64"]
```

or as environment variable, comma-separated:

```sh
SHOTS_GRINDERS=Niche Zero,DF64
```

- Home Assistant gets the select **Grinder** with these names plus **None**.
- With a single grinder it is preselected.
- The chosen grinder is sent with every shot as `grinder_model`, together with the text
  entity **Grinder setting** as `grinder_setting`. Visualizer adds the name to its
  grinder suggestions. **Unverified** until the first shot shows it.
- A new grinder = change the configuration and restart the bridge.
- Use exactly the spelling you use in Visualizer, otherwise Visualizer lists both.

## 4. Telegram message

With coffee bag and grinder set, the message reads for example:

```
☕ Silvia: shot finished
28 s, 36.2 g out from 18.0 g (1:2.0), peak 9.1 bar
…
Kenia – Kaffeekommune, grinder DF64 12
https://visualizer.coffee/shots/…
```

## Troubleshooting

| Log message | Cause |
|---|---|
| `cannot load coffee bags from Visualizer: HTTP 401` | wrong e-mail / password, or no password set (passkey only, see 1.) |
| `HTTP 429` | Visualizer rate limit (50 requests per minute); retried later |
| `coffee bag … is not active any more, selection cleared` | bag archived or deleted in Visualizer |
| `coffee_bag failed for good: … coffee management` | Premium or coffee management off |
