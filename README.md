# DevTODO - combined todo and timetracker

DevTODO is a vibecoded app made to solve the problem of having to hunt for things I was supposed to work on across Jira (for tickets) and GitLab (for merge requests). It adds the possibility of maintaining an informal TODO list just for myself in the same interface and tops the whole thing off with an integrated timetracker.

## Jira

DevTODO maintains two Jira lists: the assigned tickets list contains tickets assigned to the user. It is ranked to keep the highest priority, in sprint, and recently updated items near the top. Items waiting for other people are ranked near the bottom.

### Internal Support mode
When working in internal support, the Jira panel shows the queue of internal support tickets. There is a filter that picks only the tickets related to the component you are responsible for. 

The unassigned support tickets are ranked first by priority and then in order of creation, oldest first. This maintains a fair response time by default. Highest priority tickets show up at the top of the list even when they are unassigned. Otherwise your assigned tickets and bugs will be shown before the queue. Assigned tickets that are not bugs or support will be shown after the queue.

The unassigne tickets get a badge to point out what is assigned to you and what is just from the queue.

### The pinned epic panel
You can set a pinned epic in the settings. The unassigned tickets in pinned epic will be visible as a todo list in the UI. You can easily create child stories in it by just typing the title and pressing enter. The title of the Pinned Epic panel functions as a link to the epic.

### Download Jira as MD
There is also a funcionality to download the current Jira ticket as a markdown file

## Merge requests

The app ranks the recently worked on and actionable merge requests on top of the list. The most critical are the tickets the user is reviewing and the tickets that are ready to merge. Tickets you have already approved go to the bottom of the list to not be on the way. The rest are ranked by last updated time, keeping the most active work items at the top. 

### Small niceties
- The tool does some parsing on the merge request name, turning "XYZ-123: Merge XYZ-123-some-feature into Develop" into "XYZ-123-some-feature" with a separate field showing the target branch
- You can download merge request comments for the merge reuests assigned to you as a Markdown file.

## Time tracking 

The time tracker supports easy selection of any of the existing tasks to be worked on. You can also work on anything you want using a freeform input. The app maintains a log and at the end of the day you can create a summary, where you can add up the hours for transfer to a real time management software. The tracker also automatically clocks you out if you forgot to do it, assuming a 7.5h workday in that case.

### Small niceties
- When adding an "other work" entry, you can choose to have it start 15min before current time. Useful for when you were interrupted and want to log the interruption. 
- The summary allows you to easily add up certain items to the same total by checking the checkboxes next to the work items
- You can easily copy the summary lines one by one or all the checked ones or all the lines by clicking buttons. No need to paint anything

## Worktrees

If you work in several git worktrees in parallel — one agent or one feature per worktree — the worktree panel shows what each of them is doing without you having to visit them. List your repository paths in Settings → Worktrees (or point the scanner at the folder that contains them) and every worktree of every listed repo is discovered automatically; listing one worktree of a repo is enough to find the rest.

Each worktree shows its branch, the ticket key parsed out of the branch name, whether the working tree is clean or dirty (hover the badge for the breakdown), how far it has drifted from its upstream, and the subject and age of its last commit. The main checkout is marked, because some things have to be done there rather than in a worktree. Detached heads, locked and prunable worktrees are called out.

The panel refreshes itself every 10 seconds while the tab is visible. The order is fixed — repository, then the main checkout, then the other worktrees by name — so a refresh never shuffles a row out from under your cursor.

Each worktree has a ▶ button that starts the timer on whatever it has checked out. If the branch name carries a ticket key, the tracker looks up that ticket's summary and uses the same label the Jira panel would — so an hour clocked from the worktree panel and an hour clocked from the Jira list add up to a single line in the daily summary rather than two. Branches pointing at tickets you never loaded (someone else's, or an already closed one) are fetched on demand; branches with no ticket key are logged under the worktree and branch name.

The worktree currently holding the running timer is marked, and it is matched by ticket key — so clocking in from the Jira list also lights up the worktree that ticket's branch is checked out in, which is a quick way to find where you left the work.

### Contextual actions

The tracker bar carries the actions that apply to whatever you are currently tracking, and each worktree row carries the ones that apply to it. Tracking a ticket gets you a Jira link and a Markdown download of the ticket; if that ticket's branch is checked out somewhere, you also get one-click File Explorer, Git Bash, VS Code and Claude Code in that exact directory — which is the whole point when four worktrees are in play and you have lost track of which folder is which.

Actions that cannot apply are left out rather than shown broken, and ones that are merely unavailable say why when you hover them.

### Branches and worktrees

The main workflow is meant to be: a ticket exists, so make a branch for it and start. Tracking a ticket that has no branch anywhere gets you a **Create branch** action — name pre-filled from the ticket key and summary, base detected from the repository's own `origin/HEAD`, and a fetch first so you don't branch from a stale base. Pick which worktree it lands in; ones that are busy are listed but not selectable, with the reason shown.

Each worktree row has a **Branch…** picker. It does not list every branch — there are hundreds and the command line is there for the rest. It lists what the dashboard knows is relevant: branches for your assigned tickets, branches behind merge requests you're involved in, whatever is checked out right now, the long-lived branches, and the ten most recent. The filter box starts narrowed to the ticket you're tracking, which usually leaves exactly one row. Branches that exist only on the remote — typically someone's merge request you've been asked to review — are offered too, and checking one out creates the local tracking branch for you.

Because git refuses to check the same branch out twice, a branch held by another worktree offers **Move here** instead, which detaches the holder and checks it out where you asked. Getting a branch into the main checkout, which some tooling insists on, is a single click from the tracker bar. There is also **Release** to detach a worktree and free its branch, and per-repository buttons to add a worktree beside the main checkout or prune ones whose folders are gone.

Every one of these refuses to touch a worktree with uncommitted changes, or one where Claude was active in the last couple of minutes — pulling a branch out from under a running agent produces confident nonsense. Dialogs show the exact git commands before running them, and when git objects you get its own words, not a paraphrase. Merging, rebasing and pushing are deliberately absent: those belong in a terminal.

## Forgetting to clock in

The most common way to lose hours is to start working without starting the timer. DevTODO attacks this from both ends.

**While it is happening.** The dashboard watches your worktrees, and when one of them changes — a file saved, a commit made, a branch switched — while nothing is being tracked, a bar appears under the timer offering to start the clock on that worktree's task. If you are tracking something else, it offers to switch instead. It is a bar rather than a dialog on purpose: a popup that interrupts you mid-thought gets dismissed reflexively and teaches you to ignore it. Dismissing a nudge keeps it quiet for an hour, or until that worktree changes branch, since a new branch means a new task.

**Afterwards.** The server keeps a small activity log for every worktree, and it keeps writing whether or not the dashboard is open in a browser. Open the time report and any stretch of the day where you demonstrably worked but logged nothing is listed at the top as "untracked work" — with the times, the duration and its best guess at the label. One click adds it as a normal time entry; one click hides it if that stretch really wasn't work.

Some deliberate choices worth knowing, because they decide whether you trust the numbers:

- A stretch is only claimed when there is evidence for it. The tool would rather miss ten minutes than put an hour you did not work into a timesheet.
- Stretches are widened *backwards*, never forwards, because the work happened before the check that noticed it.
- Any time entry counts as covered, whatever it is called. If you were clocked in on "Standup" while files changed, that time is accounted for and is not reported as missing.
- Suggestions never overlap each other, so you can accept all of them without double-counting.
- A stretch that spanned several branches is labelled with the one you touched most and marked with a small `+n`; hover it to see the others.

## Pomodoro timer
The tool now has integrated pomodoro timer. The timer will keep stats on how many succesful and interrupted pomodoro periods you have logged. The tool also keeps a counter of context switches you have had today (switching between two different tasks).

## Week number
The top bar shows a week number. It's because some of our sprint tasks mention a week and the week



