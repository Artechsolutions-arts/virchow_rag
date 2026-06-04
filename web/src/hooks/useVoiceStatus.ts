export function useVoiceStatus() {
  // Voice is intentionally disabled for this local md_chunks setup.
  // Returning static values prevents repeated 404 polling to /api/voice/status.
  return {
    sttEnabled: false,
    ttsEnabled: false,
    isLoading: false,
    error: null,
  };
}
