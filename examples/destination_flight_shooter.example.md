# Destination

_Target-state specification for this project. What + why, in plain language. Technical decisions live in `adr.md`. Last reviewed: 2026-05-16 by Haakon._

## How to use this document

A destination document describes the target shape of the repo.

It is analogous to a destination we want to travel to. It describes where we want to go, not how to get there.
Think of it as a GPS coordinate that a Google Maps user is navigating toward.
In the analogy, Google Maps uses the current location and the destination to plan a route.
In our situation, the AI assistant uses the current state of the codebase and `destination.md` to plan which tasks to do.

The destination will typically contain information that we already know, and many things that we don't know the concrete answer to yet.
When the answer is already known, the document should state the decision directly.
When the answer is not yet known, the destination can name the topic or question, and specify that the destination of the repo will be that we have an answer or solution to this that is well thought through, that has a clear rationale, that is consistent with the project goals, and where the rationale behind the choice is documented in `adr.md`. In that case it will be up to autosprint to determine this choice. 

The destination document will describe the state we want to come to. It will not describe how we will come to that state. For instance, the destination document might state that we want good code quality according to best practice. It will not describe refactoring, since that is related to the journey not the destination. So even though good refactoring routines are important, it is not within the scope of the destination document to describe how to refactor — the destination document will describe the state we aim for after refactoring. Because the destination is stable while the journey is not, this document is re-read often but rewritten rarely.

> **Every parameter has a concrete answer or is explicitly open.** If you know the answer, write it directly under the section, replacing the italic prompt. If you don't, write a **destination-shaped sentence** describing what the repo will have once the question is answered, followed by an explicit `*(Open — autosprint to decide.)*` italic marker. Example for an unanswered Test strategy: *"We have a well thought-through testing strategy with a clear rationale specified in `adr.md`. (Open — autosprint to decide.)"*. The destination-shaped sentence keeps the file consistent (every section reads as a destination, not a process); the italic marker is the unambiguous signal to the planner that this section is still pending. Autosprint resolves open parameters by recording the full rationale in `adr.md`, appending a status marker at the end of the resolved section, and adding a one-line receipt to `## AI-resolved questions` at the bottom. Do not write "TBD" or invented placeholders — silent assumptions are the failure mode this rule prevents.
>
> **Section ownership.** Human-authored content lives above the `## AI-resolved questions` and `## AI-generated subgoals` headings at the bottom. Agents may append to those sections only, and may append a single status-marker blockquote at the end of a section once they've resolved that section's open question. Agents never modify the human content above those markers.
>
> **Status marker format** (used by agents when resolving an open question): `> **Status:** resolved <YYYY-MM-DD> — <one-line answer>. See ` `` `adr.md` `` ` <ADR title or date>.`
>
> **Promotion path.** When you want a resolution to graduate into the main spec, edit the original section to write the chosen answer in (replacing the prompt or "open" line), delete the status marker, and delete the receipt from `## AI-resolved questions`. The decision now reads as a normal human-authored answer; rationale stays in `adr.md` as history.

## Purpose

A small 2D arcade flight shooter playable in a window. The player pilots a plane near the bottom of the screen against a scrolling sky background, while enemy objects appear at the top and move downward. The player fires projectiles upward to shoot them down — each successful hit shows an explosion animation. Serves as an autosprint demo on a game project with explicit visual feedback for player actions.

## Users

A single human player at a keyboard who wants a few minutes of arcade-style distraction. Also: someone evaluating autosprint on a moderately ambitious game shape with timed effects (explosions are a small state machine).

## Desired behaviour

In priority order:

1. Running `python -m flight` (or `uv run flight`) opens a window with the player's plane near the bottom-centre and a scrolling background suggesting forward flight.
2. Arrow keys (or `a` / `d`) move the plane left and right within screen bounds. Optionally `w` / `s` move it up and down within a constrained vertical band.
3. `space` (or left mouse) fires a projectile that travels straight up the screen and despawns when it leaves the top of the screen.
4. Enemy objects (other planes, balloons, ground targets — pick one shape and stick to it) spawn at the top of the screen at random horizontal positions and drift downward at a steady speed.
5. When a projectile collides with an enemy, the enemy is removed, an explosion animation plays for ~0.5 seconds at the impact point, and the score increases by 10.
6. When the plane collides with an enemy directly, the plane loses one life (start: 3); a brief flash or smoke effect signals the loss.
7. The current score and remaining lives are visible at the top of the screen at all times.
8. When lives reach zero, the game freezes briefly, shows the final score, and exits cleanly.

## Out of scope

- Multiplayer or networked play.
- Sound effects or music.
- Persistent high-scores across runs.
- Multiple stages, level transitions, or boss fights.
- Heat-seeking missiles, power-ups, or special weapons (one projectile type is enough for the demo).

## Code-quality invariants

- Python 3.12+. `pygame` (or equivalent) is acceptable for rendering.
- Game logic (movement, collisions, scoring, explosion lifecycle, spawning) lives in pure functions or simple classes that can be tested without opening a real window — clean separation between the logic layer and the rendering layer.
- The full test suite (`pytest -m "not slow"`) passes on every commit.
- The explosion is a small state machine (impact → expanding → fading → done) that is fully testable as data, not pixels.

## Test strategy

Unit tests for: plane movement (within bounds, blocked at edges), projectile motion and despawn, enemy spawn-and-drift, projectile-enemy collision, plane-enemy collision, explosion state transitions, score and lives updates. A single smoke test that constructs an initial game state is enough for the rendering layer.

## AI-resolved questions

_(autosprint appends here as it answers open questions during sprints.)_

## AI-generated subgoals

_(autosprint appends product/behavioural subgoals it proposes during planning here.)_
