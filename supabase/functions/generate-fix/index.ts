import "jsr:@supabase/functions-js/edge-runtime.d.ts";

import { GoogleGenAI } from "npm:@google/genai";
import { createClient } from "npm:@supabase/supabase-js";

const ai = new GoogleGenAI({
  apiKey: Deno.env.get("GEMINI_API_KEY"),
});

const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
const supabaseServiceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const supabase = createClient(supabaseUrl, supabaseServiceKey);

const EMBED_DIMENSIONS = 768;
const MAX_MATCHES = 5;
const MAX_SOLUTION_CHARS = 1400;

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

function cleanText(value: string, maxLength = 4000) {
  return String(value || "")
    .replace(/\r/g, "")
    .replace(/\t/g, "  ")
    .replace(/\u0000/g, "")
    .trim()
    .slice(0, maxLength);
}

function escapeTripleBackticks(value: string) {
  return value.replace(/```/g, "\\`\\`\\`");
}

function buildContext(matches: any[]) {
  if (!matches?.length) return "No historical solutions found.";
  return matches
    .map((match, index) => {
      const sourceUrl = cleanText(match.source_url || "Unknown source", 500);
      const solutionText = cleanText(match.solution_text || "", MAX_SOLUTION_CHARS);
      return [
        `Match ${index + 1}`,
        `Source: ${sourceUrl}`,
        `Reference Fix: ${escapeTripleBackticks(solutionText)}`,
      ].join("\n");
    })
    .join("\n\n---\n\n");
}

function buildSources(matches: any[]) {
  if (!matches?.length) return [];
  return matches
    .map((match) => cleanText(match.source_url || "", 500))
    .filter(Boolean);
}

function buildPrompt({
  language,
  errorMessage,
  codeSnippet,
  contextString,
  sources,
}: {
  language: string;
  errorMessage: string;
  codeSnippet: string;
  contextString: string;
  sources: string[];
}) {
  const sourceList = sources.length
    ? sources.map((url) => `- ${url}`).join("\n")
    : "- No external references found";

  return `
You are a senior software debugging assistant.

Your job is to produce a clean, polished Markdown answer for a ${language} error.

Follow these rules exactly:

- Return valid Markdown only.
- Do not wrap the full response in triple backticks.
- Use the exact section headings shown below.
- Be concise, specific, and practical.
- Do not say "here is the markdown" or add any intro text before the first heading.
- If code is included, use a fenced code block with the language tag ${language.toLowerCase()}.
- Use bullet points where useful, but do not overdo it.
- Do not repeat the user's full error message unless needed.
- Under Sources, only include the provided source URLs that are actually relevant.
- If the retrieved context is weak, still provide your best fix.

You must return Markdown in exactly this structure:

# Summary
One short paragraph explaining the root cause in plain English.

# Likely Fix
Brief explanation then one fenced code block if applicable.

# Why It Broke
2-5 bullet points.

# What To Check Next
2-4 bullet points with concrete next steps.

# Sources
Bullet list of relevant URLs only. If none, write exactly:
- No relevant sources found

Project language: ${language}

Error message:
${errorMessage}

Code snippet:
${codeSnippet || "No code snippet provided."}

Retrieved reference material:
${contextString}

Available source URLs:
${sourceList}
`.trim();
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  try {
    const body = await req.json();
    const errorMessage = cleanText(body?.errorMessage, 3000);
    const codeSnippet = cleanText(body?.codeSnippet || "", 8000);
    const language = cleanText(body?.language, 50);

    if (!errorMessage || !language) {
      throw new Error("Missing required fields: errorMessage and language are required.");
    }

    const embeddingResponse = await ai.models.embedContent({
      model: "gemini-embedding-001",
      contents: errorMessage,
      config: {
        taskType: "RETRIEVAL_QUERY",
        outputDimensionality: EMBED_DIMENSIONS,
      },
    });

    const queryVector = embeddingResponse.embeddings?.[0]?.values;
    if (!queryVector?.length) throw new Error("Failed to generate query embedding.");

    const { data: vectorMatches, error: dbError } = await supabase.rpc("match_solutions_v2", {
      query_embedding: queryVector,
      match_count: MAX_MATCHES,
      filter_language: language.toLowerCase(),
    });

    if (dbError) throw dbError;

    const sources = buildSources(vectorMatches || []);
    const contextString = buildContext(vectorMatches || []);
    const prompt = buildPrompt({ language, errorMessage, codeSnippet, contextString, sources });

    const generationResponse = await ai.models.generateContent({
      model: "gemini-3.1-flash-lite-preview",
      contents: prompt,
      config: { temperature: 0.3, topP: 0.9 },
    });

    const markdown = cleanText(generationResponse.text || "", 20000);
    if (!markdown) throw new Error("The model returned an empty response.");

    return new Response(
      JSON.stringify({ success: true, solution: markdown, format: "markdown", sources }),
      { headers: { ...corsHeaders, "Content-Type": "application/json" }, status: 200 }
    );
  } catch (error: any) {
    const msg = error?.message || error?.details || error?.toString() || "Unknown error";
    console.error("Function execution failed:", msg);
    return new Response(
      JSON.stringify({ success: false, error: msg }),
      { headers: { ...corsHeaders, "Content-Type": "application/json" }, status: 400 }
    );
  }
});