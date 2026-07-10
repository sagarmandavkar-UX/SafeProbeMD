# SafeProbeMD LLM Integration Plan

## 1. Current System

SafeProbeMD currently uses deterministic agents by default. The deterministic mode is intended for reproducible evaluation of extraction, bounded routing, claim provenance, verification, safety fallback, and consensus behavior.

## 2. Future LLM Mode

Future LLM mode should be optional and implemented behind a provider interface. The orchestrator should be able to select deterministic agents or LLM-backed agents without changing the Patient Snapshot schema or routing boundaries.

The first interface should accept:

- agent name
- bounded input packet
- JSON response schema
- timeout and retry configuration
- model/provider metadata for audit logs

## 3. Supported Future Providers

- Local Ollama
- OpenAI-compatible endpoint
- vLLM server

Provider support should be configured through environment variables and runtime settings, not hard-coded credentials.

## 4. Token-Saving Strategy

- Bounded agent packets only
- No full snapshot fanout
- Note section truncation
- Structured JSON responses
- Verification before consensus

The LLM path should preserve the same routing contract as deterministic mode. Agents should receive only the fields needed for their role, and consensus should operate on verified claims rather than raw patient content.

## 5. Research Comparison

Compare deterministic agents against LLM-backed agents using:

- unsupported claim rate
- contradiction rate
- human-review rate
- estimated token cost
- runtime

Secondary analyses can stratify cases by ICU status, missing-data burden, high-acuity flags, and note availability.

## 6. Safety

No API call should receive the raw full MIMIC dataset, unrestricted tables, or a raw full Patient Snapshot unless that routing boundary has been explicitly approved for a specific research protocol.

No API keys should be stored in code, committed files, notebooks, or examples. Use environment variables only, keep real data in approved access-controlled environments, and log only aggregate counts/rates when creating shareable artifacts.
