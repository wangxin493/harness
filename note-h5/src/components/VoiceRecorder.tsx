import { useState } from 'react';
import { Button, Space, Typography } from 'antd';
import { useVoice } from '@/hooks/useVoice';

const { Text } = Typography;

interface VoiceRecorderProps {
  onTranscription: (text: string) => void;
}

export function VoiceRecorder({ onTranscription }: VoiceRecorderProps) {
  const { isRecording, startRecording, stopRecording } = useVoice();
  const [error, setError] = useState<string | null>(null);

  const handleStart = async () => {
    try {
      setError(null);
      await startRecording();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start recording');
    }
  };

  const handleStop = async () => {
    try {
      setError(null);
      await stopRecording();
      onTranscription('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to stop recording');
    }
  };

  return (
    <Space direction="vertical" size="small">
      <Space>
        <Button
          type={isRecording ? 'primary' : 'default'}
          danger={isRecording}
          onClick={isRecording ? handleStop : handleStart}
        >
          {isRecording ? 'Stop Recording' : 'Start Recording'}
        </Button>
        {isRecording && <Text type="warning">Recording...</Text>}
      </Space>
      {error && <Text type="danger">{error}</Text>}
    </Space>
  );
}