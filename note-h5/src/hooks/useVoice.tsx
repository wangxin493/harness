import { useState, useCallback } from "react";
import { voiceService } from "@/api/voiceService";

export function useVoice() {
  const [isRecording, setIsRecording] = useState(false);
  const [transcript, setTranscript] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const startRecording = useCallback(async () => {
    try {
      setError(null);
      setTranscript(null);
      await voiceService.startRecording();
      setIsRecording(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start recording");
    }
  }, []);

  const stopRecording = useCallback(async () => {
    try {
      setError(null);
      await voiceService.stopRecording();
      setIsRecording(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to stop recording");
    }
  }, []);

  const transcribeAudio = useCallback(async (audioBlob: Blob) => {
    try {
      setError(null);
      const result = await voiceService.transcribeAudio(audioBlob);
      setTranscript(result);
      return result;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Transcription failed");
      return null;
    }
  }, []);

  const getTranscript = useCallback(() => {
    return transcript;
  }, [transcript]);

  return { isRecording, transcript, error, startRecording, stopRecording, transcribeAudio, getTranscript };
}