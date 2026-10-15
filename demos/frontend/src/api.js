export async function fetchMessage() {
  const response = await fetch('/api/message');
  if (!response.ok) throw new Error(`/api/message: ${response.status}`);
  return response.json();
}
