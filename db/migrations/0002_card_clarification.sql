-- A card that asks a clarifying question has to remember it.
--
-- Without this the question is shown, the person answers "oil", and the
-- next request arrives as the single word "oil" with nothing to attach it
-- to. The card asks again, or guesses. Storing the exchange on the card
-- keeps the existing discipline: the card's state is the context, not a
-- conversation transcript.
ALTER TABLE app.card
    ADD COLUMN IF NOT EXISTS pending_clarification jsonb;
