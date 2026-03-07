#include <stdlib.h>
#include <string.h>

void	destroy_data(char *data)
{
	if (data)
		free(data);
}

typedef struct s_node
{
	char			*data;
	struct s_node	*next;
}	t_node;

t_node	*create_node(const char *str)
{
	t_node	*n;

	n = malloc(sizeof(t_node));
	n->data = malloc(strlen(str) + 1);
	strcpy(n->data, str);
	n->next = NULL;
	return (n);
}

t_node	*build_list(int count)
{
	t_node	*head;
	t_node	*current;
	int		i;

	head = create_node("node_0");
	current = head;
	i = 1;
	while (i < count)
	{
		current->next = create_node("node_x");
		current = current->next;
		i++;
	}
	
	return (head);
}

void	partial_cleanup(t_node *list)
{
	t_node	*rest;

	if (!list)
		return ;
	rest = list->next;
	destroy_data(list->data);
	free(list);
	(void)rest;
}

int	main(void)
{
	t_node	*list;

	list = build_list(4);
	partial_cleanup(list);

	
	return (0);
}
