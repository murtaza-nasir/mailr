// The assistant's name outside React, with no imports: strings read when a module loads and
// strings built outside a component use it (src/i18n, {{name}}). useAssistantName.ts keeps it current.

export const DEFAULT_ASSISTANT_NAME = 'Ambrose' // i18n-ignore: the product's name for the assistant

// The name the last useAssistantName() saw, for text built outside a component (toasts, errors,
// static lists). One name in every user-facing string: never "the assistant" or "Ask".
let lastName = DEFAULT_ASSISTANT_NAME

/** Called by useAssistantName() with the name it shows. */
export function rememberAssistantName(name: string): void {
  lastName = name
}

/** The assistant's name outside a component: the one the app last showed, else "Ambrose". */
export function assistantNameNow(): string {
  return lastName
}

/** Static text written with "Ambrose" (shortcut descriptions, tour tips), with the configured name. */
export function withAssistantName(text: string, name: string = lastName): string {
  return name === DEFAULT_ASSISTANT_NAME ? text : text.replace(/\bAmbrose\b/g, name)
}
