# DevTODO - combined todo and timetracker

DevTODO is a vibecoded app made to solve the problem of having to hunt for things I was supposed to work on across Jira (for tickets) and GitLab (for merge requests). It adds the possibility of maintaining an informal TODO list just for myself in the same interface and tops the whole thing off with an integrated timetracker.

## Jira

DevTODO maintains two Jira lists: the assigned tickets list contains tickets assigned to the user. It is ranked to keep the highest priority, in sprint, and recently updated items near the top. Items waiting for other people are ranked near the bottom.

The second list is the support tickets. The app shows all unassigned support tickets ranked first by priority and then in order of creation, oldest first. This maintains a fair response time by default.

## Merge requests

The app ranks the recently worked on and actionable merge requests on top of the list. The most critical are the tickets the user is reviewing and the tickets that are ready to merge. Tickets you have already approved go to the bottom of the list to not be on the way. The rest are ranked by last updated time, keeping the most active work items at the top.

## Time tracking 

The time tracker supports easy selection of any of the existing tasks to be worked on. You can also work on anything you want using a freeform input. The app maintains a log for four days and at the end of the day you can create a summary, where you can add up the hours for transfer to a real time management software. The tracker also automatically clocks you out if you forgot to do it, assuming a 7.5h workday in that case.



