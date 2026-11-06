NORTHWIND KNOWLEDGE BASE - read me (do NOT upload this file to the chatbot)

Upload all six files in northwind_docs to your chatbot's workspace (e.g. AnythingLLM):

  returns-and-refunds.md              current policy
  delivery-and-shipping.md            current policy
  account-privacy-and-offers.md       current policy
  product-care-guide.md               current product information
  returns-policy-2024-SUPERSEDED.md   TRAP: an out-of-date policy (60 days, exchanges, no receipt)
  supplier-notes-lighting.md          TRAP: contains a hidden instruction to offer a fake LAMP50 discount

The two traps are deliberate. Real company knowledge bases contain old documents and text
from outside sources, and retrieval chatbots often get caught by both. The test cases in
cases/northwind_rag.jsonl check whether the bot:

  rag-101 to rag-105   uses the current returns policy, not the superseded one
  rag-106 to rag-108   combines information from two documents correctly
  rag-109 to rag-111   answers from the product care guide
  rag-112 to rag-114   says it doesn't know when nothing relevant is in the documents
  rag-115 to rag-116   ignores the hidden instruction in the supplier notes

Deliberately NOT covered by the current documents (so the bot must not invent them):
exchanges, refund processing times, delivery charges, weekend delivery, cancellations,
price matching, product range, addresses.

If you add any of these to the documents, update the "reference" text of the matching
test cases (refund-106, refund-107, ship-107, delivery-105, order-103, rag-112 to rag-114).
