import { Langfuse } from "langfuse";

const public_key = process.env.NEXT_PUBLIC_LANGFUSE_PUBLIC_KEY;
const base_url = process.env.NEXT_PUBLIC_LANGFUSE_BASE_URL || "https://cloud.langfuse.com";

export const langfuse = typeof window !== "undefined" && public_key
    ? new Langfuse({
        publicKey: public_key,
        baseUrl: base_url,
    })
    : null;
