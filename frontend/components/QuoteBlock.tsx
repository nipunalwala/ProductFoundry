import { SOURCE_NAMES } from "@/lib/exports";
import type { Quote } from "@/lib/exports";

/** A review quote in its own words, its translation if it is Hinglish, and a link to its source. */
export function QuoteBlock({ quote }: { quote: Quote }) {
  const where = `${SOURCE_NAMES[quote.source] ?? quote.source}, ${quote.date}`;
  return (
    <blockquote className="quote">
      <p>{quote.text}</p>
      {quote.translation ? (
        <p className="translation">Translation: {quote.translation}</p>
      ) : null}
      <footer>
        {quote.product},{" "}
        {quote.url ? (
          <a href={quote.url} target="_blank" rel="noreferrer">
            {where}
          </a>
        ) : (
          where
        )}
        {quote.rating ? `, ${quote.rating}/5 stars` : ""}
      </footer>
    </blockquote>
  );
}
