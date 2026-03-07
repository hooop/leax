#include <stdlib.h>
#include <string.h>


char	**create_array(int count)
{
	char	**arr;
	int		i;

	arr = malloc(sizeof(char *) * count);
	i = 0;
	while (i < count)
	{
		arr[i] = malloc(16);
		strcpy(arr[i], "item");
		i++;
	}
	return (arr);
}

void	cleanup(char **arr, int count)
{
	int	i;

	i = 0;
	while (i < count - 1)
	{
		free(arr[i]);
		i++;
	}
	free(arr);
	
}

int	main(void)
{
	char	**data;

	data = create_array(5);
	cleanup(data, 5);
	return (0);
}
