import { Eye, EyeOff, LogIn, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";
import { errorMessage, login, type LoginResponse } from "../api";
import brandMark from "../assets/scenara-mark.svg";
import { Tooltip } from "../components/Tooltip";

type LoginProps = {
  onLoggedIn: (session: LoginResponse) => void;
};

export function Login({ onLoggedIn }: LoginProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [revealPassword, setRevealPassword] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!username || !password || pending) {
      return;
    }
    setPending(true);
    setError("");
    try {
      const session = await login(username, password);
      onLoggedIn(session);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPending(false);
    }
  };

  return (
    <main className="login-page">
      <section className="login-brand-pane" aria-label="scenara model">
        <div className="login-brand-lockup">
          <img src={brandMark} alt="" />
          <div><strong>scenara model</strong><span>景枢模型平台</span></div>
        </div>
        <div className="login-brand-message">
          <p>视觉 AI 中枢平台</p>
          <h1>连接视觉<br />理解世界</h1>
        </div>
        <div className="login-brand-footer">
          <ShieldCheck size={17} />
          <span>统一身份与访问控制</span>
        </div>
      </section>

      <section className="login-form-pane">
        <div className="login-mobile-brand">
          <img src={brandMark} alt="" />
          <div><strong>scenara model</strong><span>景枢模型平台</span></div>
        </div>

        <form className="login-form" aria-label="登录" onSubmit={submit}>
          <header>
            <p>scenara model</p>
            <h2>登录模型平台</h2>
            <span>使用你的 scenara model 账号登录。</span>
          </header>

          <div className="login-field">
            <label htmlFor="username">用户名</label>
            <input
              id="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              autoFocus
              required
            />
          </div>

          <div className="login-field">
            <label htmlFor="password">密码</label>
            <span className="login-password-field">
              <input
                id="password"
                type={revealPassword ? "text" : "password"}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                required
              />
              <Tooltip content={revealPassword ? "隐藏密码" : "显示密码"} placement="top">
                <button
                  type="button"
                  aria-label={revealPassword ? "隐藏密码" : "显示密码"}
                  onClick={() => setRevealPassword((visible) => !visible)}
                >
                  {revealPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </Tooltip>
            </span>
          </div>

          {error ? <p className="login-error" role="alert">{error}</p> : null}

          <button className="login-submit" type="submit" disabled={pending || !username || !password}>
            <LogIn size={18} />
            {pending ? "正在登录" : "登录"}
          </button>
        </form>

        <footer className="login-form-footer">scenara model · 景枢模型平台</footer>
      </section>
    </main>
  );
}
