# DevTODO - combined todo and timetracker

DevTODO is a vibecoded app made to solve the problem of having to hunt for things I was supposed to work on across Jira (for tickets) and GitLab (for merge requests). It adds the possibility of maintaining an informal TODO list just for myself in the same interface and tops the whole thing off with an integrated timetracker.

## Jira

DevTODO maintains two Jira lists: the assigned tickets list contains tickets assigned to the user. It is ranked to keep the highest priority, in sprint, and recently updated items near the top. Items waiting for other people are ranked near the bottom.

The second list is the support tickets. The app shows all unassigned support tickets ranked first by priority and then in order of creation, oldest first. This maintains a fair response time by default.

### The pinned epic panel
You can set a pinned epic in the settings. The unassigned tickets in pinned epic will be visible as a todo list in the UI. You can easily create child stories in it by just typing the title and pressing enter. The title of the Pinned Epic panel functions as a link to the epic.

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

## Pomodoro timer
The tool now has integrated pomodoro timer. The timer will keep stats on how many succesful and interrupted pomodoro periods you have logged. The tool also keeps a counter of context switches you have had today (switching between two different tasks).

## Week number
The top bar shows a week number. It's because some of our sprint tasks mention a week and the week



