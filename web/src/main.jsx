// main.jsx — Preact entry. Mounts <App/> into #workspace and pulls in global styles
// (esbuild emits dist app.css from the imported stylesheet).
import { render } from 'preact';
import './styles.css';
import { App } from './app.jsx';

render(<App />, document.getElementById('workspace'));
