import React from 'react';
import { createRoot } from 'react-dom/client';

function App() {
  return (
    <main style={{ padding: 24, fontFamily: 'Arial, sans-serif' }}>
      <h1>Note H5</h1>
      <p>项目已准备好，可以运行和继续由 Harness 生成业务代码。</p>
    </main>
  );
}

const container = document.getElementById('root');

if (!container) {
  throw new Error('Root element #root not found');
}

createRoot(container).render(<App />);
