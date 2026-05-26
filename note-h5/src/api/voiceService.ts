import { NoteInput } from "@/types";

interface VoiceService {
  startRecording(): Promise<void>;
  stopRecording(): Promise<Blob | null>;
  transcribeAudio(audioBlob: Blob): Promise<string>;
}

class VoiceServiceImpl implements VoiceService {
  private mediaRecorder: MediaRecorder | null = null;
  private audioChunks: Blob[] = [];
  private isRecording: boolean = false;

  async startRecording(): Promise<void> {
    if (this.isRecording) {
      throw new Error("Already recording");
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this.mediaRecorder = new MediaRecorder(stream);
      this.audioChunks = [];
      this.isRecording = true;

      this.mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          this.audioChunks.push(event.data);
        }
      };

      this.mediaRecorder.start();
    } catch (error) {
      this.isRecording = false;
      throw new Error("Failed to start recording: " + (error instanceof Error ? error.message : "Unknown error"));
    }
  }

  async stopRecording(): Promise<Blob | null> {
    if (!this.isRecording || !this.mediaRecorder) {
      return null;
    }
    return new Promise((resolve, reject) => {
      this.mediaRecorder!.onstop = () => {
        const audioBlob = new Blob(this.audioChunks, { type: "audio/webm" });
        this.audioChunks = [];
        this.isRecording = false;
        // Stop all tracks to release microphone
        this.mediaRecorder!.stream.getTracks().forEach(track => track.stop());
        resolve(audioBlob);
      };
      this.mediaRecorder!.onerror = (event) => {
        this.isRecording = false;
        reject(new Error("Recording error"));
      };
      this.mediaRecorder!.stop();
    });
  }

  async transcribeAudio(audioBlob: Blob): Promise<string> {
    // Simulate transcription using Web Speech API or mock
    // For now, return a mock transcription
    return new Promise((resolve) => {
      setTimeout(() => {
        resolve("This is a simulated transcription of the audio.");
      }, 1000);
    });
  }
}

export const voiceService: VoiceService = new VoiceServiceImpl();