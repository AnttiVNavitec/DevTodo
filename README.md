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

## Pomodoro timer
The tool now has integrated pomodoro timer. The timer will keep stats on how many succesful and interrupted pomodoro periods you have logged. The tool also keeps a counter of context switches you have had today (switching between two different tasks).

## Week number
The top bar shows a week number. It's because some of our sprint tasks mention a week and the week



