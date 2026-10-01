export function joinVoiceDraft(baseText: string, transcript: string): string {
  if (!baseText) return transcript;
  if (!transcript) return baseText;
  if (/\s$/.test(baseText) || /^\s/.test(transcript)) return `${baseText}${transcript}`;
  if (/[A-Za-z0-9]$/.test(baseText) && /^[A-Za-z0-9]/.test(transcript)) {
    return `${baseText} ${transcript}`;
  }
  return `${baseText}${transcript}`;
}
