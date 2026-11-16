import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <section>
      <h2>Page Not Found</h2>
      <p>Return to the login page and continue from there.</p>
      <p>
        <Link to="/login">Go to Login</Link>
      </p>
    </section>
  );
}

