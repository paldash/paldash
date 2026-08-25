// Conventional Commits: `type(scope): summary`, lowercase summary, body free.
// Checked on pull requests in ci.yml; run by hand with
//   npx commitlint --from origin/main
export default {
  extends: ['@commitlint/config-conventional'],
  rules: {
    // Subjects here are sentences with em dashes and explain the *why*; the
    // default 100 is enough for that without inviting paragraphs.
    'header-max-length': [2, 'always', 100],
    // Bodies are prose wrapped by hand. A pasted log line or a URL should not
    // fail the commit, so this is a warning rather than an error.
    'body-max-line-length': [1, 'always', 100],
  },
};
