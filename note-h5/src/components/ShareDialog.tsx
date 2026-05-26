import React, { useState, useEffect } from 'react';
import { Modal, Button, Select, Input, Space, message, List, Tag, Typography } from 'antd';
import { CopyOutlined, DeleteOutlined } from '@ant-design/icons';
import { useShare } from '@/hooks/useShare';
import type { ShareLink } from '@/types';

const { Text } = Typography;

interface ShareDialogProps {
  noteId: string;
  visible: boolean;
  onClose: () => void;
}

export function ShareDialog({ noteId, visible, onClose }: ShareDialogProps) {
  const { shareLinks, loading, createShareLink, deleteShareLink } = useShare();
  const [permission, setPermission] = useState<'view' | 'edit'>('view');
  const [generatedUrl, setGeneratedUrl] = useState<string>('');

  useEffect(() => {
    if (visible && noteId) {
      // 分享链接在 shareLinks 中已加载
    }
  }, [visible, noteId]);

  const handleGenerate = async () => {
    try {
      const link = await createShareLink(noteId, permission);
      setGeneratedUrl(link.url);
      message.success('分享链接已生成');
    } catch (err) {
      message.error('生成分享链接失败');
    }
  };

  const handleCopy = (url: string) => {
    navigator.clipboard.writeText(url).then(() => {
      message.success('链接已复制');
    }).catch(() => {
      message.error('复制失败');
    });
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteShareLink(id);
      message.success('分享已删除');
    } catch (err) {
      message.error('删除失败');
    }
  };

  return (
    <Modal
      title="分享笔记"
      open={visible}
      onCancel={onClose}
      footer={null}
      destroyOnClose
    >
      <Space direction="vertical" style={{ width: '100%' }}>
        <Space>
          <Select
            value={permission}
            onChange={setPermission}
            style={{ width: 120 }}
            options={[
              { value: 'view', label: '只读' },
              { value: 'edit', label: '可编辑' },
            ]}
          />
          <Button type="primary" onClick={handleGenerate} loading={loading}>
            生成链接
          </Button>
        </Space>
        {generatedUrl && (
          <Space>
            <Input value={generatedUrl} readOnly style={{ width: 300 }} />
            <Button icon={<CopyOutlined />} onClick={() => handleCopy(generatedUrl)}>
              复制
            </Button>
          </Space>
        )}
        <Text strong>已有分享记录</Text>
        <List
          loading={loading}
          dataSource={shareLinks}
          renderItem={(item: ShareLink) => (
            <List.Item
              actions={[
                <Button
                  type="link"
                  icon={<CopyOutlined />}
                  onClick={() => handleCopy(item.url)}
                />,
                <Button
                  type="link"
                  danger
                  icon={<DeleteOutlined />}
                  onClick={() => handleDelete(item.id)}
                />,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space>
                    <Text copyable={{ text: item.url }}>{item.url}</Text>
                    <Tag color={item.permission === 'edit' ? 'blue' : 'green'}>
                      {item.permission === 'edit' ? '可编辑' : '只读'}
                    </Tag>
                  </Space>
                }
                description={`创建于 ${new Date(item.createdAt).toLocaleString()}`}
              />
            </List.Item>
          )}
        />
      </Space>
    </Modal>
  );
}