import { useEffect, useState } from 'react';
import { fetchMessage } from './api.js';

export function App() {
  const [message, setMessage] = useState(null);
  useEffect(() => {
    fetchMessage()
      .then((data) => setMessage(data.message))
      .catch(() => setMessage('(backend unreachable)'));
  }, []);
  return (
    <main>
      <h1>claude-devcontainer demo</h1>
      <p>backend says: {message ?? 'loading…'}</p>
    </main>
  );
}
