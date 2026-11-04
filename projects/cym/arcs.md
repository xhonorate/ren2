# Arcs

## Overall

**Shape: hub calendar over two terms** (DESIGN.md §8). 98 scenes.

- **Act 1 — common (8 scenes, the first two weeks of autumn term).** He
  wakes; Natalie, Diana and Claire in the hospital; move-in day with Bella
  and Alice; first day at college with Yuki and Victoria; Natalie's first
  home visit; the diner breakfast where Diana lays out the investigation;
  the end of the second week, when the building and the college have
  become a shape he can walk through. Every woman has had one scene that
  is hers. The last common scene ends on the first free evening.
- **Act 2 — the calendar (18 hubs + 1 break scene + 63 spokes).** Autumn
  hubs, weeks 3–10 (week 3 the first cold night, 4 Halloween, 6 river fog,
  7 first snow, 9 exams, 10 last day of term); then one common winter-break
  scene (the building at Christmas; everyone is around, nobody is picked);
  then spring hubs, weeks 11–20 (11 January ice, 13 the thaw, 15 the river
  high, 17 blossom on the college steps, 19 exams, 20 the last day). Each
  hub is a short common scene that carries the season and ends on the
  choice of whose evening it is; the pick calls that route's next spoke,
  which returns to the hub. A route has 9 spokes. Nothing closes another
  route until the player chooses.
- **Act 3 — the true route (7 finales + 1 lock).** The game always ends on
  one woman. When a route's ninth spoke returns to the hub, the hub asks,
  plainly, whether she is the one: *Make her your true route?* Yes: the
  calendar ends and her **finale** plays, a celebration lap of a scene,
  love and happiness and the register at its fullest (level 5). No: the
  calendar goes on with everyone else, and she stays in his life as she
  is; she can still be chosen at the end. The last day of spring term is
  the fallback lock: every completed route is offered once more; if none
  was completed, the alone ending, which is kind. Affection is not a gate
  on the finale; it colours lines along the way and in it.

**What a route is for.** Each route is about *her*: who she is, what made
her, what she wants from him and why. Every route has backstory beats
where he learns something about her nobody else at the college or in the
building knows (marked *about her* below). The player's own mystery (the
night, his mother) runs under three routes only — Diana, Claire and
Victoria — and is never the point of the others; the bible pins its truth
so those three agree.

### The night (bible truth; the game states only pieces of it)
Two weeks before the story, clearing his father's study, he found a box of
letters from his mother: one every birthday since he was three, addressed
to him, opened, kept from him. His step-mother had intercepted them. The
night of the "accident" he confronted her, packed a bag to leave, and on
the stairs she grabbed for the bag and he fell. She waited hours before
calling it in as a fall; that delay is what the hospital flagged. He
remembers the letters and the bag. He does not remember the stairs. The
letters are in his belongings, which Diana holds. The investigation is
open; the step-mother is barred from contact; she does not accept that.

### His mother (bible truth)
Alive, two towns over, a music teacher. She left when he was three, during
a long illness she has since recovered from; his father remarried; the
step-mother made sure she stayed gone. She has written every year. She was
at college with Victoria.

**Affection.** Every spoke has one or two choices worth +1/+2 (about 18
available per route). It never gates the finale; it changes lines, who
says the word first, and how the finale is coloured. Route completion is
`flags.<id>_step == 9`; a hub option calls `<id>_s{step+1}`; the hub asks
the true-route question when a call returns with step 9 and jumps to the
finale on yes (`flags.<id>_true`).

**Intimacy (DESIGN.md §7).** Every scene has a level 0–5. Each route has
one first intimate scene (level 2, more PG than what follows); after it,
every scene on that route is level 3 or higher and its explicit moment
arises from what is happening in the scene. Levels only rise. The rate is
per character: Bella and Natalie come on strong (first intimate scene at
spoke 2, level 4 by spoke 4); Alice and Victoria at spoke 3; Claire and
Diana at spoke 4; Yuki at spoke 4 and slowest to climb. The register
(mommy-domme, in her flavour) is hers to offer and his to accept; the
spoke where he accepts it is marked.

Ladder notation below: `L 0 1 1 2 3 4 4 5 5 | F5` = levels for spokes 1–9
and the finale.

---

## diana

*Premise:* the woman whose job is to make sure he is looked after decides,
one overstep at a time, that the person looking after him is her.
*Pace:* slow. `L 0 1 1 2 3 4 4 5 5 | F5`.
*About her:* foster care and what it left on her back; why she does the
job; the cases she calls her children. *Mystery:* the father's withdrawn
complaint; the letters from evidence; the step-mother at the door.

1. **Groceries** (0). She arrives with bags and cooks in his kitchen; "my
   boy" slips out and she pretends it didn't. Choice: let her stay for
   dinner, or send her home with the leftovers.
2. **The file** (1). Her office. She tells him more than she should: a
   complaint years ago, from his father, withdrawn. She fixes his collar
   before he leaves and her hand stays a second too long.
3. **The interview** (1). She drives him to the police interview and waits
   outside. In the car park afterwards he shakes; she holds him across the
   handbrake until he stops, and does not let go when he does. On the
   drive back she tells him, flatly, why she does this job: the homes she
   grew up in, and the one worker who came back. *About her.*
4. **The dress** (2, first intimate). Dinner "to celebrate the lease" in
   the one dress she owns. She names the line she has crossed. At his
   door she kisses him, slowly, and goes home.
5. **Rain** (3). She gets caught in it bringing him a lamp; a towel, his
   bed, "just until I'm warm". The first time, slow and mostly under the
   covers; the maternal register begins because he asks for it, and she
   has been waiting to be asked. *He accepts the register.*
6. **Sunday** (4). Her place, her kitchen, the whole day. The register
   arrives in full and she says the word first; he says it back.
7. **The door** (4). The step-mother comes to the building. Diana is there
   before anyone has called her; she stands in the doorway with the flat
   voice and the woman leaves. Upstairs she shakes, and he holds her for
   the first time instead of the other way round; then she needs him and
   says so, and the scene is as much hers as his. She gives him the box of
   letters from evidence.
8. **The bath** (5). She runs it, washes his hair, and the register is in
   daylight now; and for the first time she lets him see her back, and
   tells him which scar is which. *About her.* Afterwards she reads him
   the first letter.
9. **The file closes** (5). The investigation's result arrives. The case
   could close and she tells him what she wants instead of a closed file,
   and asks him about the summer.

*Finale — the summer at Diana's.* The key on the hook is his. "My boy",
said in daylight, and he says the word back; a day that is nothing but
being looked after, and the night that follows.

## claire

*Premise:* the therapist assigned to his trauma recovers the night with him
and loses, on purpose, the distance that let her do it.
*Pace:* slow, rule-bound. `L 0 1 1 2 3 4 4 5 5 | F5`.
*About her:* the bullying, the long grey year, what the goth was for, the
tattoos and what each marks. *Mystery:* the memory itself (letters at 4,
stairs at 7); the letter to his mother.

1. **The hour** (0). Sessions; the tissue box he refuses; she extends the
   hour and bills for fifty minutes.
2. **The laundrette** (1). They meet off-hours by accident; she is in the
   casual goth, not the dress, and talks about herself for the first time:
   the bullying, the long grey year, why psychology. She folds his shirts
   without asking and their hands keep meeting in the basket.
3. **The river** (1). One in the morning on the river path, where it
   turns out she walks too, most nights. She takes his arm without
   comment and walks him back; at his door, the almost. (The witch
   costume belongs to the Halloween hub, week 4.)
4. **The letters** (2, first intimate). Memory work in her office. The box
   comes back, the bag, his step-mother's hand. Not the stairs. She holds
   him; afterwards she says, flat and precise, that she should not have,
   and that she will again, and kisses him once to prove it.
5. **Referred** (3). She refers him to a colleague first, and tells him
   why, and only then opens her door to him. "Let it out" is hers; the
   soothing register is what she has always wanted to give someone and
   never been allowed to. *He accepts the register.*
6. **Two a.m.** (4). The nightmare; he calls; she comes in the goth dress
   over pajamas and stays. Waiting for him to sleep she tells him what the
   black clothes were for at fifteen: armour, then a uniform, then just
   hers. *About her.* The soothing becomes nursing; she talks him down and
   then talks him through it.
7. **The stairs** (4). The rest of the memory returns on the river path.
   She gets him home and holds him through the shaking, and afterwards,
   for the first time, she offers "mama" out loud and waits.
8. **Ink** (5). She shows him the back tattoo and what it marks (the year
   she survived), then the rest, one by one, each with its year. *About
   her.* He traces them; everything she has kept covered is his to see.
9. **The letter** (5). They write to his mother together at her table and
   post it before either of them can reconsider. What follows is a
   celebration.

*Finale — the summer.* The reply comes in June; he reads it with his head
in her lap, and she reads him, and the rest of the day is hers to give.

## natalie

*Premise:* the nurse with eleven minutes gives him twelve, then an evening,
then the one thing she wants, which is something easy.
*Pace:* fast and clinical. `L 1 2 3 4 4 4 5 5 5 | F5`.
*About her:* eldest of five, everyone's mother at twelve, which is where
the efficiency comes from and why nobody has ever looked after her; ten
years of wards; why she stopped dating. She is also the one who called it
in, which is a fact about her before it is a clue.

1. **Twelve minutes** (1). Home visit; she clocks the not-sleeping as a
   symptom and prescribes walking. She checks his pupils closer than the
   job requires. Choice: argue, or walk.
2. **Insomnia** (2, first intimate). She treats it her way, matter-of-fact
   and faintly bored: "this is the most efficient way to make you sleep."
   Hands, clinical and charged; it works. *He accepts the register.*
3. **The river** (3). She walks with him after a shift, in the casual
   clothes, and tells him what she flagged and why. "Hours. Not minutes."
   And why she noticed: four younger siblings, and a mother who was not
   there, and a twelve-year-old who learned to count. *About her.* On the
   bench her head is on his shoulder, then her hand is not.
4. **After nights** (4). Her place at seven in the morning. Simple.
   "Mommy" is a tease that lands harder than she expected.
5. **The ward** (4). He brings her coffee at work. The supply closet; four
   minutes; her idea; she times it.
6. **Day off** (4). Her flat for the first time: four photographs on the
   fridge, all grown now, none of them calling. *About her.* Then the
   diner in the casual clothes, the booth, under the table, her face
   perfectly still.
7. **Discharge** (5). The last official visit; the clipboard signed off.
   She has to decide whether to keep coming with no reason. She comes back
   the next day without the clipboard and makes the point thoroughly.
8. **Night shift** (5). He falls asleep in her bed waiting for her; she
   wakes him her way.
9. **Days** (5). She takes a week of day shifts, the first time she has
   been awake at nine in the evening in years, and asks him for something
   for the first time instead of prescribing it. *About her.* She gets it.

*Finale — a day off.* The diner at six a.m., her asleep on his shoulder
in the booth, the first time anyone has seen her off duty; then a whole
day with nowhere to be and someone, for once, looking after her, in the
only way she accepts.

## alice

*Premise:* the neighbour who wanted a child and got a wall between her and
one, and the question of what he lets her be.
*Pace:* warm and quick once started. `L 1 1 2 3 4 4 4 5 5 | F5`.
*About her:* the two men who nearly married her and why they left; the
children she wanted; the paintings turned to the wall; nine years in 2B.

1. **Soup** (1). Dinner through the wall becomes dinner at hers. "Knock
   any time." The hug at the door lasts too long and she apologises for
   it. Choice: knock that night, or not.
2. **The boxes** (1). She cleans his place (she does the whole building)
   and finds the photograph: his mother, holding him. She cries, and says
   why: she wanted this, a baby on her hip, and the window closed. *About
   her.* He doesn't cry, and she holds him anyway, on the floor, for a
   long time.
3. **The wall** (2, first intimate). Pajamas, midnight, he knocks.
   Cuddling on the sofa with too many cushions; "you can call me whatever
   you like, honey." He tries the word, and she kisses him for it.
   *He accepts the register.*
4. **Flour** (3). Baking in the maid uniform because she came straight
   from cleaning 3C; flour everywhere; "let Mommy clean you up." Short,
   sweet, giggling.
5. **Hers** (4). Nursing, spoiling, his pleasure first; the baby talk is
   sincere and he stops being embarrassed by it halfway through.
6. **The storm** (4). Power out across the building; candles; her bed
   because it is warmer. By candlelight she tells him about the two men,
   the one who wanted no children and the one who wanted a different
   woman, and that she stayed in 2B because it was the last place she was
   happy. *About her.* Nobody sleeps.
7. **The booth** (4). The diner, Sunday; she feeds him from her plate and
   her foot finds him under the table while she talks to the waitress.
8. **Move in** (5). She asks. Her fears come out in order: old,
   unattractive, left. He answers each one with his hands as much as his
   words; the choice is which he answers first.
9. **The second bedroom** (5). She shows him the room she never used, the
   paintings turned to the wall, and turns them round: nine years of
   painting the family she did not get. *About her.* The register at its
   fullest and most tender.

*Finale — the summer.* The boxes come through the wall; 2A is empty; she
says "Mommy's home" with the shopping and means every word, and the
second bedroom gets painted, and the evening is spent the way she has
always wanted to spend one.

## bella

*Premise:* the landlady who despises her tenants goes upstairs to check on
one, and finds a boy who likes being told what to do.
*Pace:* fast and hard. `L 1 2 3 3 4 4 4 5 5 | F5`.
*About her:* raised by her grandparents in the ground-floor flat that is
now her office; the parents who left; the building she was going to sell
and could not; the gym as the only place that is not the building.

1. **The first** (1). Rent day. She brings a space heater "so you stop
   running up my bill", makes him carry it up, and watches him do it.
2. **Six a.m.** (2, first intimate). She orders him to the gym. He goes.
   The stretch, "look at you", and the changing room door she leaves open
   while she talks to him. *He accepts the register.*
3. **The books** (3). Her office, evening; the building is losing money;
   he helps and she hates that he can. The ledger is in her grandmother's
   handwriting and she says so, once, and does not say anything else about
   it. *About her.* The first order he obeys that has nothing to do with
   rent, under the desk, and he chooses it out loud.
4. **Inspection** (3). She inspects 2A with a clipboard she does not need.
   Orders. Her feet on his table; what she tells him to do about it.
5. **The roof** (4). The aerial; her grandfather taught her to fix it and
   she tells him so while he holds the ladder. *About her.* He nearly
   slips and she catches him by the collar. Adrenaline, the whole town
   below, and her way of making sure he is fine.
6. **Her terms** (4). The degradation is offered as a question and taken
   as an answer; it is hers alone, and the narration never joins in.
7. **My building** (4). The step-mother at the front door. Bella throws
   her out with four words and the master keys in her fist. Upstairs, the
   softest she has ever been, for about a minute, and then she needs to be
   in charge of something.
8. **After hours** (5). She has keys to the gym. Mirrors. Everything she
   has said she would do.
9. **The lease** (5). She tears it up in front of him in the ground-floor
   flat, and tells him who lived there and that she has not slept in it
   since. *About her.* "You don't pay rent. You pay me." What that means,
   in detail.

*Finale — the summer.* She stops calling him kid and does not notice she
has; the ground-floor flat gets slept in again; and she spends a whole
day being exactly as soft as she wants to be, which is more than a minute.

## yuki

*Premise:* the classmate who has been through the same system finds, in
him, the first person she can say things to; the gap between what she can
say and what she has thought is the whole route.
*Pace:* slowest to start, surprising once started. `L 0 1 1 2 3 3 4 4 5 | F5`.
*About her:* the homes, aging out, the sewing machine that came with her
from the last one; the conventions where nobody knows her; the list.

1. **The window seat** (0). The library; studying; the stutter on the
   first word of everything that matters.
2. **The dress form** (1). Her apartment, the cosplay half-made, the bunny
   suit under a sheet. She tells him about aging out, that Diana still
   checks on her, and that Diana does that for no one else. "Except you."
3. **The long way** (1). The river path; she says the thing in a rush and
   hides in her cardigan. Holding hands all the way back. The choice is
   whether he lets her hide.
4. **The convention** (2, first intimate). She asks him to come. The bunny
   suit worn in a hall where nobody knows her; behind the curtain she
   kisses him before she can think about it.
5. **The list** (3). Study night; she has written a list of things she
   wants to try, and reads him the first one, and tries it, and needs
   telling it was good. *He accepts the register, which she apologises for
   offering.*
6. **Locked out** (3). Bella's keys are nowhere; the stairwell at
   midnight; she dares herself, because nobody can see, and then somebody
   nearly does.
7. **Hers** (4). "Mommy" is her idea, said into his shoulder; inexperienced
   dominance that needs reassurance at every step, and gets it.
8. **The seminar** (4). She presents, because he is in the room. Victoria
   says one dry thing. Afterwards, the empty seminar room, and all that
   adrenaline with nowhere else to go.
9. **The whole list** (5). She shows him the rest of it. They start at the
   top.

*Finale — the summer.* A convention in another city, the suit she made,
and one person in the hall who knows her; and the hotel afterwards, and
the second sentence, which turns out to be "stay".

## victoria

*Premise:* the professor who decided on him before she knew why, and the
reason, which is his mother.
*Pace:* measured, then absolute. `L 1 1 2 3 4 4 4 5 5 | F5`.
*About her:* the professor who kept her after class at twenty and left;
the two men who were unfaithful; why she decided to own instead of trust.
*Mystery:* she knew his mother; she finds her and arranges the meeting.

1. **The footnotes** (1). She keeps him after the seminar; a reading list;
   a seat assigned; his collar straightened with two fingers. "Boy."
2. **The register** (1). She recognises his surname and says nothing. The
   control tightens: office hours, reading aloud, her hand resting on the
   back of his neck while he does.
3. **The photograph** (2, first intimate). Dinner at hers in the evening
   clothes. She tells him she knew his mother at college and shows him a
   photograph he has never seen. She decides when the evening ends; it
   ends with his head in her lap, clothed, her fingers in his hair.
4. **Corrections** (3). Office hours; the essay; every mark on it is an
   instruction and she sees that each one is carried out. *He accepts the
   register.*
5. **Owned** (4). Everything is hers and she says so; the yandere seam
   shows as devotion with teeth.
6. **The lake** (4). She takes him away for a weekend where nobody knows
   either of them; the swimsuit; a cabin; a rule for every hour. On the
   second night she tells him about the professor who kept her after
   class at twenty, and the two men after him, and the day she decided she
   would rather own than trust. *About her.*
7. **The drawer** (4). She shows him his essays, every one, kept; and the
   timetable; and what she is. He has to decide whether that frightens
   him. The choice is the route's hinge.
8. **Arranged** (5). She has found his mother and arranged a meeting
   without asking him, because that is what she does. The fight, and the
   making up, which she conducts.
9. **Permission** (5). She asks him for something for the first time,
   instead of telling him. He gives it.

*Finale — the meeting.* His mother, in person, in the diner, with
Victoria waiting in the car and not coming in until she is asked; and
then she is asked, and the evening after is the first thing in her life
she has not planned.

## Rejected
- Exclusive routes with a single early lock: simpler to write, but it
  makes "whose evening" a one-time choice instead of the game.
- A separate "Fund"/benefactor mystery from the earlier premise draft:
  dropped; the step-mother and the letters carry the plot.
- Five-spoke routes in one term: too short for the intimacy to build
  scene by scene; replaced by nine spokes over two terms.
- Good/normal endings gated on affection: replaced by one finale per
  route, chosen by the player with a yes/no when her route completes.
- A mystery clue on every route: dropped; routes are about her.
